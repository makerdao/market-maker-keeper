/*!
 * This file is part of Maker Keeper Framework.
 *
 * Copyright (C) 2017 reverendus
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

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
