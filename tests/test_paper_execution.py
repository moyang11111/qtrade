from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import uuid
from pathlib import Path

import pandas as pd
import pytest

from paper_trading.automation import AutoPaperCoordinator
from paper_trading.clock import TradingClock
from paper_trading.engine import PaperTradingEngine, calc_tax
from paper_trading.market_data import MarketDataProvider


DATES = ('2026-09-28','2026-09-29','2026-09-30','2026-10-08','2026-10-09')


class Time:
    value = datetime(2026,9,30,10)
    def __call__(self):
        return self.value


class Service:
    live = True
    active_pipeline = None
    def __init__(self, now):
        self.now = now
        self.as_of = '2026-09-29'
        self.price = 12.0
        self.quote_date = None
        self.candidates = []
    @property
    def universe_summary(self):
        return {'source':'test','as_of':self.as_of,'total':1}
    def get_info(self, symbol):
        timestamp = self.quote_date or self.now().strftime('%Y-%m-%d %H:%M:%S')
        return dict(name='fixture',latest=self.price,open=self.price,high=self.price+0.1,low=self.price-0.1,
                    prev_close=self.price,volume=100000,time=timestamp,change_pct=0.0)
    def load_history(self, symbol):
        return pd.DataFrame({'open':[10,10,12],'high':[10.1,10.1,12.1],'low':[9.9,9.9,11.9],
                             'close':[10,10,12],'volume':[100000]*3},index=pd.to_datetime(DATES[:3])).loc[:self.as_of]
    def _resolve_df(self,symbol,count=320):
        return self.load_history(symbol)
    def mainboard_symbols(self):
        return ['000001']
    def is_tradable(self,symbol):
        return True
    def set_candidate_symbols(self,symbols):
        self.candidates = symbols


class Lock:
    _fh = object()
    def __init__(self,*args):
        pass
    def acquired(self):
        return True


class Signals:
    SIGNAL_MODES = {'test':'test','other':'other'}
    MAX_POSITIONS = 8
    POS_RATIO = 0.1
    TAKE_PROFIT = 0.1
    STOP_LOSS = 0.08
    COST = 0.0015
    def __init__(self):
        self.state = {}
    def _signal_for(self,df,**kwargs):
        return {'action':'buy','price':10,'date':str(df.index[-1])[:10],'reason':'均线买入'}
    @staticmethod
    def _mainboard_scan(service,symbols,rs_min):
        return {},1.0,symbols


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    now = Time()
    service = Service(now)
    clock = TradingClock(now,lambda:DATES)
    engine = PaperTradingEngine(str(tmp_path/'account.db'),MarketDataProvider(service),clock=clock)
    engine.create_account('a',100000)
    return now,service,clock,engine


def coordinator(tmp_path,now,service,clock,reports=None):
    trader = AutoPaperCoordinator(service=service,state_dir=tmp_path/'auto',signal_engine=Signals,
                                 lock_factory=Lock,rs_thresholds={'test':0},clock=clock,report_provider=reports)
    trader.set_mode('test',service)
    trader.toggle(service)
    return trader


@pytest.mark.parametrize('time',[
    datetime(2026,10,2,10),datetime(2026,9,30,12),datetime(2026,9,30,18),
    datetime(2026,9,30,9,29),datetime(2026,9,30,14,58)])
def test_closed_session_cannot_fill_even_with_same_day_quote(setup,time):
    now,service,clock,engine = setup
    now.value = time
    with pytest.raises(ValueError,match='交易时段'):
        engine.trade_at_quote('a','000001','buy',100)
    assert engine.list_trades('a') == []


@pytest.mark.parametrize('live,quote_date',[(False,'2026-09-29 15:00:00'),(True,'2026-09-29 15:00:00'),
    (True,'2026-09-30 09:00:00'),(True,'2026-09-30 10:01:00')])
def test_historical_csv_or_stale_quotes_cannot_create_backdated_lots(setup,live,quote_date):
    now,service,clock,engine = setup
    service.live,service.quote_date = live,quote_date
    with pytest.raises(ValueError,match='过期'):
        engine.trade_at_quote('a','000001','buy',100)
    assert engine.get_positions('a') == []


def test_t_plus_one_uses_actual_execution_date(setup):
    now,service,clock,engine = setup
    engine.trade_at_quote('a','000001','buy',100)
    with pytest.raises(ValueError,match=r'T\+1'):
        engine.trade_at_quote('a','000001','sell',100)
    with engine._connect() as conn:
        assert conn.execute('SELECT acquired_date FROM position_lots').fetchone()[0]=='2026-09-30'
    now.value = datetime(2026,10,8,10)
    order=engine.trade_at_quote('a','000001','sell',100)
    assert order['status']=='filled' and order['created_at'].startswith('2026-10-08')


def test_duplicate_execution_key_is_one_fill_under_concurrency_and_restart(setup,tmp_path):
    now,service,clock,engine = setup
    engines = [engine,PaperTradingEngine(str(tmp_path/'account.db'),MarketDataProvider(service),clock=clock)]
    def buy(i):
        return engines[i%2].trade_at_quote('a','000001','buy',100,intent_key='same-key',metadata={'source':'策略'})
    with ThreadPoolExecutor(max_workers=4) as pool:
        orders=list(pool.map(buy,range(8)))
    assert len({o['order_id'] for o in orders})==1
    assert len(engine.list_trades('a'))==1
    with engine._connect() as conn:
        meta=json.loads(conn.execute('SELECT payload FROM paper_position_meta').fetchone()[0])
    assert meta['buy_date']=='2026-09-30'


def test_fill_and_metadata_rollback_together(setup):
    now,service,clock,engine=setup
    before=engine.get_account('a')['cash']
    with pytest.raises(TypeError):
        engine.trade_at_quote('a','000001','buy',100,intent_key='rollback',metadata={'bad':object()})
    assert engine.get_account('a')['cash']==before
    assert engine.list_trades('a')==[]
    with engine._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0]==0


def test_signal_queues_after_close_and_executes_next_day_price_once(setup,tmp_path):
    now,service,clock,engine=setup
    now.value=datetime(2026,9,29,16)
    trader=coordinator(tmp_path,now,service,clock)
    after_close=trader.cycle(service)
    assert after_close['pending_count']==1 and after_close['position_count']==0
    assert after_close['execution_date']=='2026-09-30'
    now.value=datetime(2026,9,30,10)
    result=trader.cycle(service)
    assert result['positions'][0]['buy_price']==12.0
    assert result['positions'][0]['buy_time'].startswith('2026-09-30')
    trader.cycle(service)
    assert len(trader.engine.list_trades('default'))==1


def test_old_snapshot_cannot_replay_trades_after_target_day(setup,tmp_path):
    now,service,clock,engine=setup
    now.value=datetime(2026,10,8,10)
    trader=coordinator(tmp_path,now,service,clock)
    result=trader.cycle(service)
    assert result['position_count']==0 and result['pending_count']==0
    assert '已结束' in result['last_error']


def test_decision_and_strategy_share_peak_drawdown_and_family_gate(setup,tmp_path):
    now,service,clock,engine=setup
    trader=coordinator(tmp_path,now,service,clock)
    trader.state['equity_peak']=150000
    trader._save_meta()
    result=trader.buy_from_decision(service,{'code':'000001','reason':'均线买入'})
    assert result['position_count']==0 and '回撤' in result['last_error']
    trader.state['equity_peak']=100000
    trader._save_meta()
    trader._family_counts=lambda:{'trend':4}
    result=trader.buy_from_decision(service,{'code':'000001','reason':'均线买入'})
    assert result['position_count']==0 and '因子族' in result['last_error']


def test_decision_after_close_reports_queued_without_fake_fill(setup,tmp_path):
    now,service,clock,engine=setup
    now.value=datetime(2026,9,29,16)
    trader=coordinator(tmp_path,now,service,clock)
    result=trader.buy_from_decision(service,{'code':'000001','reason':'买入'})
    assert result['decision_queued'] and result['position_count']==0


def test_stamp_tax_reduced_rate():
    assert calc_tax('sell',10000)==5.0
    assert calc_tax('buy',10000)==0.0


def report():
    return {'state':'complete','report':dict(as_of='2026-09-29',target_date='2026-09-30',model_id='test',
        generated_at='2026-09-29T16:00:00',predictions=[dict(rank=i,symbol=f'{i:06d}',research_probability=.6) for i in range(1,101)])}


def test_first_frozen_ranking_is_immutable_and_next_day_labels_separate(setup,tmp_path):
    now,service,clock,engine=setup
    now.value=datetime(2026,9,29,16,5)
    original=report()
    trader=coordinator(tmp_path,now,service,clock,lambda:original)
    trader.cycle(service)
    assert trader._research_status()[0]['prospective'] is True
    original['report']['predictions'][0]['symbol']='999999'
    trader.cycle(service)
    with trader.engine._connect() as conn:
        saved=json.loads(conn.execute('SELECT payload FROM paper_research_freezes').fetchone()[0])
    assert saved['predictions'][0]['symbol']=='000001'
    now.value=datetime(2026,9,30,16)
    service.as_of='2026-09-30'
    trader.cycle(service)
    verified=trader._research_status()[0]
    assert verified['outcome']['top100']=={'up':100,'down':0,'flat':0}
    assert verified['outcome']['rows'][0]['symbol']=='000001'


def test_retrospective_ranking_never_counts_as_prospective(setup,tmp_path):
    now,service,clock,engine=setup
    now.value=datetime(2026,10,2,10)
    old=report()
    old['report']['generated_at']='2026-10-02T09:38:25'
    trader=coordinator(tmp_path,now,service,clock,lambda:old)
    trader.cycle(service)
    assert trader._research_status()[0]['prospective'] is False


def test_calendar_failure_is_closed(setup):
    now,service,clock,engine=setup
    engine.clock.loader=lambda:[]
    with pytest.raises(ValueError,match='日历不可用'):
        engine.trade_at_quote('a','000001','buy',100)


def test_legacy_json_import_is_once_and_reset_cannot_resurrect_positions(setup,tmp_path):
    now,service,clock,engine=setup
    folder=tmp_path/'auto'
    folder.mkdir()
    legacy=folder/'auto_paper_state.json'
    legacy.write_text(json.dumps({'cash':90000,'running':True,'signal_mode':'test',
        'positions':{'000001':{'qty':100,'buy_price':10,'buy_date':'2026-09-28'}}}),encoding='utf-8')
    trader=AutoPaperCoordinator(service=service,state_dir=folder,signal_engine=Signals,
                               lock_factory=Lock,rs_thresholds={'test':0},clock=clock)
    assert trader.status(service)['cash']==90000 and trader.status(service)['position_count']==1
    assert trader.status(service)['running'] is False
    trader.reset(service)
    restarted=AutoPaperCoordinator(service=service,state_dir=folder,signal_engine=Signals,
                                  lock_factory=Lock,rs_thresholds={'test':0},clock=clock)
    assert restarted.status(service)['position_count']==0 and restarted.status(service)['cash']==100000
    assert legacy.exists()


def test_database_and_json_migration_preserves_cash_trades_and_pauses(setup,tmp_path):
    now,service,clock,engine=setup
    old=PaperTradingEngine(str(tmp_path/'auto_paper_state.db'),MarketDataProvider(service),clock=clock)
    old.create_account('default',100000)
    old.trade_at_quote('default','000001','buy',100)
    (tmp_path/'auto_paper_meta.json').write_text(json.dumps({'running':True,'signal_mode':'test'}),encoding='utf-8')
    folder=tmp_path/'new-state'
    trader=AutoPaperCoordinator(service=service,state_dir=folder,signal_engine=Signals,
                               lock_factory=Lock,rs_thresholds={'test':0},clock=clock)
    assert trader.status(service)['cash']==old.get_account('default')['cash']
    assert len(trader.engine.list_trades('default'))==1
    assert trader.status(service)['running'] is False
    assert old.get_positions('default')[0]['qty']==100


def test_partial_sell_does_not_mutate_lots_when_sellable_insufficient(setup):
    now,service,clock,engine=setup
    engine.trade_at_quote('a','000001','buy',100)
    now.value=datetime(2026,10,8,10)
    with pytest.raises(ValueError):
        engine.trade_at_quote('a','000001','sell',200)
    assert engine.get_positions('a')[0]['qty']==100
    assert len(engine.list_trades('a'))==1


def test_same_cycle_concurrent_runs_do_not_duplicate_queue_or_fill(setup,tmp_path):
    now,service,clock,engine=setup
    now.value=datetime(2026,9,29,16)
    trader=coordinator(tmp_path,now,service,clock)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _:trader.cycle(service),range(3)))
    assert trader.status(service)['pending_count']==1
    now.value=datetime(2026,9,30,10)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _:trader.cycle(service),range(3)))
    assert len(trader.engine.list_trades('default'))==1


def test_controls_require_post_and_same_origin_without_real_user_state(setup,tmp_path,monkeypatch):
    import server
    from http.server import ThreadingHTTPServer
    import threading
    import requests
    now,service,clock,engine=setup
    trader=coordinator(tmp_path,now,service,clock)
    monkeypatch.setattr(server,'AUTO_PAPER',trader)
    monkeypatch.setattr(server,'SERVICE',service)
    monkeypatch.setattr(server,'STATIC_DIR',tmp_path)
    http=ThreadingHTTPServer(('127.0.0.1',0),server.APIHandler)
    worker=threading.Thread(target=http.serve_forever,daemon=True)
    worker.start()
    base=f'http://127.0.0.1:{http.server_port}'
    session=requests.Session()
    session.trust_env=False
    try:
        assert session.get(base+'/api/auto/paper?action=status',timeout=5).status_code==200
        assert session.get(base+'/api/auto/paper?action=toggle',timeout=5).status_code==405
        assert session.post(base+'/api/auto/paper?action=reset',headers={'Origin':'https://foreign.example'},timeout=5).status_code==403
        response=session.post(base+'/api/auto/paper?action=toggle',headers={'Origin':base,'Sec-Fetch-Site':'same-origin'},timeout=5)
        assert response.status_code==200 and response.json()['running'] is False
        assert trader.engine.list_trades('default')==[]
    finally:
        http.shutdown()
        http.server_close()
        worker.join(timeout=5)
        session.close()


def manual_request(**changes):
    return dict(symbol='000001',side='buy',qty=100,request_id=str(uuid.uuid4())) | changes


def test_manual_buy_add_partial_sell_and_t_plus_one_share_ledger(setup,tmp_path):
    now,service,clock,_ = setup
    trader = coordinator(tmp_path,now,service,clock)
    trader.set_execution_mode('manual',service)
    first = trader.manual_trade(service,**manual_request())
    assert first['positions'][0]['source']=='手动'
    assert first['positions'][0]['sellable_qty']==0
    service.price = 14.0
    second = trader.manual_trade(service,**manual_request())
    assert second['positions'][0]['qty']==200 and second['positions'][0]['buy_price']==13.0
    with pytest.raises(ValueError,match=r'T\+1'):
        trader.manual_trade(service,symbol='000001',side='sell',qty=100,request_id=str(uuid.uuid4()))
    now.value = datetime(2026,10,8,10)
    result = trader.manual_trade(service,symbol='000001',side='sell',qty=100,request_id=str(uuid.uuid4()))
    assert result['positions'][0]['qty']==100 and result['positions'][0]['buy_price']==14.0
    assert len(trader.engine.list_trades('default'))==3
    assert result['running'] is False


def test_manual_request_is_idempotent_after_restart_and_rejects_changed_payload(setup,tmp_path):
    now,service,clock,_ = setup
    trader = coordinator(tmp_path,now,service,clock)
    trader.set_execution_mode('manual',service)
    request = manual_request()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _:trader.manual_trade(service,**request),range(3)))
    assert len({result['submitted_order']['order_id'] for result in results})==1
    restarted = AutoPaperCoordinator(service=service,state_dir=tmp_path/'auto',signal_engine=Signals,lock_factory=Lock,rs_thresholds={'test':0},clock=clock)
    now.value = datetime(2026,10,2,10)
    assert restarted.manual_trade(service,**request)['submitted_order']['order_id']==results[0]['submitted_order']['order_id']
    with pytest.raises(ValueError,match='不同'):
        restarted.manual_trade(service,**{**request,'qty':200})
    assert len(restarted.engine.list_trades('default'))==1


@pytest.mark.parametrize('change,match',[
    ({'qty':150},'100股'),({'qty':True},'正整数'),({'qty':100.5},'正整数'),
    ({'symbol':'１２３４５６'},'6位'),({'side':'short'},'买入或卖出'),({'request_id':'x'},'标识')])
def test_manual_rejects_invalid_orders_without_account_mutation(setup,tmp_path,change,match):
    now,service,clock,_ = setup
    trader = coordinator(tmp_path,now,service,clock)
    trader.set_execution_mode('manual',service)
    with pytest.raises(ValueError,match=match):
        trader.manual_trade(service,**{**manual_request(),**change})
    assert trader.engine.list_trades('default')==[]
    assert trader.status(service)['cash']==100000


def test_manual_rejects_closed_stale_insufficient_cash_and_drawdown(setup,tmp_path):
    now,service,clock,_ = setup
    trader = coordinator(tmp_path,now,service,clock)
    trader.set_execution_mode('manual',service)
    now.value = datetime(2026,10,2,10)
    with pytest.raises(ValueError,match='休市'):
        trader.manual_trade(service,**manual_request())
    now.value = datetime(2026,9,30,10)
    service.quote_date='2026-09-29 15:00:00'
    with pytest.raises(ValueError,match='过期'):
        trader.manual_trade(service,**manual_request())
    service.quote_date=None
    with pytest.raises(ValueError,match='资金不足'):
        trader.manual_trade(service,**{**manual_request(),'qty':100000})
    trader.state['equity_peak']=150000
    trader._save_meta()
    with pytest.raises(ValueError,match='回撤'):
        trader.manual_trade(service,**manual_request())
    assert trader.engine.list_trades('default')==[]


def test_manual_is_independent_of_strategy_breadth_and_does_not_run_strategy(setup,tmp_path):
    now,service,clock,_ = setup
    trader = coordinator(tmp_path,now,service,clock)
    trader.set_execution_mode('manual',service)
    trader.state['l0_gate']=False
    trader._save_meta()
    result = trader.manual_trade(service,**manual_request())
    assert result['position_count']==1
    assert trader.cycle(service)['pending_count']==0
    assert trader.cycle(service,force=True)['pending_count']==0
    with pytest.raises(ValueError,match='手动模式'):
        trader.toggle(service)


def test_confirm_mode_requires_approval_and_survives_restart(setup,tmp_path):
    now,service,clock,_ = setup
    now.value = datetime(2026,9,29,16)
    trader = coordinator(tmp_path,now,service,clock)
    trader.set_execution_mode('confirm',service)
    result = trader.cycle(service,force=True)
    assert result['approval_count']==1 and result['pending_count']==0
    intent = result['pending_orders'][0]['intent_id']
    now.value = datetime(2026,9,30,10)
    trader.toggle(service)
    assert trader.cycle(service)['position_count']==0
    restarted = AutoPaperCoordinator(service=service,state_dir=tmp_path/'auto',signal_engine=Signals,lock_factory=Lock,rs_thresholds={'test':0},clock=clock)
    result = restarted.review_intent(service,intent,True)
    assert result['position_count']==1 and result['approval_count']==0
    with pytest.raises(ValueError,match='已处理'):
        restarted.review_intent(service,intent,True)
    assert len(restarted.engine.list_trades('default'))==1


def test_reject_expire_cancel_and_mode_switch_never_execute_old_suggestions(setup,tmp_path):
    now,service,clock,_ = setup
    now.value = datetime(2026,9,29,16)
    trader = coordinator(tmp_path,now,service,clock)
    trader.set_execution_mode('confirm',service)
    result = trader.cycle(service,force=True)
    intent = result['pending_orders'][0]['intent_id']
    assert trader.review_intent(service,intent,False)['order_history'][0]['status']=='rejected'
    trader.set_execution_mode('auto',service)
    result = trader.cycle(service,force=True)
    intent = result['pending_orders'][0]['intent_id']
    assert trader.cancel_intent(service,intent)['pending_count']==0
    trader.set_execution_mode('confirm',service)
    result = trader.cycle(service,force=True)
    now.value = datetime(2026,9,30,15)
    assert trader.cycle(service)['approval_count']==0
    assert trader.status(service)['order_history'][0]['status']=='expired'
    assert trader.engine.list_trades('default')==[]


def test_approval_while_paused_waits_then_mode_switch_cancels(setup,tmp_path):
    now,service,clock,_ = setup
    now.value = datetime(2026,9,29,16)
    trader = coordinator(tmp_path,now,service,clock)
    trader.set_execution_mode('confirm',service)
    result = trader.cycle(service,force=True)
    intent = result['pending_orders'][0]['intent_id']
    now.value = datetime(2026,9,30,10)
    assert trader.review_intent(service,intent,True)['pending_count']==1
    assert trader.cycle(service)['position_count']==0
    result = trader.set_execution_mode('auto',service)
    assert result['pending_count']==0 and result['order_history'][0]['status']=='cancelled'
    assert result['running'] is False


def test_confirmation_applies_to_exit_signals_and_reset_cancels_reviews(setup,tmp_path):
    now,service,clock,_ = setup
    now.value=datetime(2026,9,29,10)
    trader=coordinator(tmp_path,now,service,clock)
    trader.set_execution_mode('manual',service)
    trader.manual_trade(service,**manual_request())
    trader.set_execution_mode('confirm',service)
    trader._signals._signal_for=lambda df,**kwargs:{'action':'sell','price':10,'date':'2026-09-29','reason':'卖出建议'}
    now.value=datetime(2026,9,29,16)
    result=trader.cycle(service,force=True)
    assert result['pending_orders'][0]['side']=='sell'
    now.value=datetime(2026,9,30,10)
    trader.toggle(service)
    assert trader.cycle(service)['position_count']==1
    intent=result['pending_orders'][0]['intent_id']
    assert trader.review_intent(service,intent,True)['position_count']==0
    trader.set_execution_mode('auto',service)
    trader.set_execution_mode('confirm',service)
    trader.cycle(service,force=True)
    assert trader.reset(service)['approval_count']==0


@pytest.fixture
def paper_http(setup,tmp_path,monkeypatch):
    import server
    import threading
    import requests
    from http.server import ThreadingHTTPServer
    now,service,clock,_=setup
    trader=coordinator(tmp_path,now,service,clock)
    monkeypatch.setattr(server,'AUTO_PAPER',trader)
    monkeypatch.setattr(server,'SERVICE',service)
    http=ThreadingHTTPServer(('127.0.0.1',0),server.APIHandler)
    worker=threading.Thread(target=http.serve_forever,daemon=True)
    worker.start()
    base=f'http://127.0.0.1:{http.server_port}'
    session=requests.Session()
    session.trust_env=False
    session.headers.update({'Origin':base,'Sec-Fetch-Site':'same-origin'})
    try:
        yield base,session,trader,service,now
    finally:
        http.shutdown()
        http.server_close()
        worker.join(timeout=5)
        session.close()


def test_manual_http_contract_and_duplicate_request(paper_http):
    base,session,trader,service,now=paper_http
    url=base+'/api/auto/paper?action='
    assert session.get(url+'manual_trade').status_code==405
    assert session.post(url+'set_execution_mode',json={'mode':'manual'}).status_code==200
    request=manual_request()
    assert session.post(url+'manual_trade',json=request,headers={'Origin':'https://foreign.example'}).status_code==403
    response=session.post(url+'manual_trade',json=request)
    assert response.status_code==200 and response.json()['submitted_order']['status']=='filled'
    assert session.post(url+'manual_trade',json=request).status_code==200
    assert len(trader.engine.list_trades('default'))==1
    assert session.post(url+'manual_trade',json={**request,'qty':150}).status_code==400


@pytest.mark.parametrize('body',['{}','[]','{"mode":"manual","mode":"auto"}','{"mode":null}','{"mode":"manual","extra":true}','{'])
def test_paper_http_invalid_json_never_changes_mode(paper_http,body):
    base,session,trader,service,now=paper_http
    response=session.post(base+'/api/auto/paper?action=set_execution_mode',data=body,headers={'Content-Type':'application/json'})
    assert response.status_code==400
    assert trader.status(service)['execution_mode']=='auto'
