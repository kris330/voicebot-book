
// 自动选择 ws:// 或 wss://
const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
const wsUrl = `${wsProtocol}//${location.host}/ws`;
const socket = new WebSocket(wsUrl);
