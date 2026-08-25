'use strict';

const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('qtradeDesktop', Object.freeze({
  platform: process.platform,
}));
