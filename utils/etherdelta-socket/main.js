const io = require('socket.io-client');
const readline = require('readline');

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
  terminal: false
});

const socket = io.connect(process.argv[2], { transports: ['websocket'] });
socket.on('connect', () => {
  console.log('Socket connected');
});

socket.on('disconnect', () => {
  console.log('Socket disconnected');
});

socket.on('reconnect', () => {
  console.log('Socket reconnected');
});

socket.on('messageResult', (messageResult) => {
  console.log(messageResult);
});

rl.on('line', function (line) {
  const order = JSON.parse(line);
  console.log("Sending message to place an order");
  socket.emit('message', order);
});
