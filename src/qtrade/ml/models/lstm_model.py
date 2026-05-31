"""PyTorch LSTM model for time-series prediction."""

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from qtrade.ml.models.base import BaseModel

logger = logging.getLogger("qtrade.ml.models.lstm")


class LSTMModel(BaseModel):
    """LSTM model using PyTorch for sequence-based prediction."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.hidden_size = config.get("hidden_size", 64)
        self.num_layers = config.get("num_layers", 2)
        self.dropout = config.get("dropout", 0.3)
        self.epochs = config.get("epochs", 50)
        self.batch_size = config.get("batch_size", 32)
        self.seq_length = config.get("sequence_length", 20)
        self.learning_rate = config.get("learning_rate", 0.001)
        self._model = None
        self._scaler = None

    def _build_model(self, input_size: int):
        import torch
        import torch.nn as nn

        class _LSTM(nn.Module):
            def __init__(self, input_size, hidden_size, num_layers, dropout):
                super().__init__()
                self.lstm = nn.LSTM(input_size, hidden_size, num_layers,
                                    batch_first=True, dropout=dropout)
                self.fc = nn.Linear(hidden_size, 2)

            def forward(self, x):
                out, _ = self.lstm(x)
                return self.fc(out[:, -1, :])

        return _LSTM(input_size, self.hidden_size, self.num_layers, self.dropout)

    def _to_sequences(self, X: pd.DataFrame) -> np.ndarray:
        """Convert tabular data to sequences."""
        data = X.values
        seqs = []
        for i in range(len(data) - self.seq_length + 1):
            seqs.append(data[i:i + self.seq_length])
        return np.array(seqs)

    def _fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        import torch
        import torch.nn as nn
        from sklearn.preprocessing import StandardScaler

        # Scale features
        self._scaler = StandardScaler()
        X_scaled = self._scaler.fit_transform(X)
        X_scaled_df = pd.DataFrame(X_scaled, columns=X.columns, index=X.index)

        # Create sequences
        seqs = self._to_sequences(X_scaled_df)
        labels = y.values[self.seq_length - 1:]

        if len(seqs) == 0:
            logger.warning("Not enough data for LSTM sequences")
            return

        input_size = X.shape[1]
        self._model = self._build_model(input_size)
        optimizer = torch.optim.Adam(self._model.parameters(), lr=self.learning_rate)
        criterion = nn.CrossEntropyLoss()

        self._model.train()
        for epoch in range(self.epochs):
            total_loss = 0
            for i in range(0, len(seqs), self.batch_size):
                batch_x = torch.FloatTensor(seqs[i:i + self.batch_size])
                batch_y = torch.LongTensor(labels[i:i + self.batch_size])
                optimizer.zero_grad()
                output = self._model(batch_x)
                loss = criterion(output, batch_y)
                loss.backward()
                optimizer.step()
                total_loss += loss.item()
            if (epoch + 1) % 10 == 0:
                logger.debug("LSTM epoch %d/%d, loss: %.4f",
                             epoch + 1, self.epochs, total_loss / max(1, len(seqs) // self.batch_size))

    def _predict(self, X: pd.DataFrame) -> np.ndarray:
        import torch

        if self._model is None:
            raise RuntimeError("Model not trained!")

        X_scaled = self._scaler.transform(X)
        seqs = self._to_sequences(pd.DataFrame(X_scaled, columns=X.columns))

        self._model.eval()
        with torch.no_grad():
            x_tensor = torch.FloatTensor(seqs)
            output = self._model(x_tensor)
            preds = output.argmax(dim=1).numpy()

        # Pad beginning with zeros to match input length
        full_preds = np.zeros(len(X))
        full_preds[self.seq_length - 1:] = preds
        return full_preds

    def save(self, path: Path) -> None:
        import torch
        state = {"model_state": self._model.state_dict() if self._model else None,
                 "scaler": self._scaler, "config": self._config}
        torch.save(state, path)

    def load(self, path: Path) -> None:
        import torch
        state = torch.load(path, weights_only=False)
        self._scaler = state["scaler"]
        input_size = state.get("config", {}).get("input_size", 13)
        self._model = self._build_model(input_size)
        if state["model_state"]:
            self._model.load_state_dict(state["model_state"])
