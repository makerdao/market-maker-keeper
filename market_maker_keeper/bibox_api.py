# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2017 reverendus
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import hashlib
import hmac
import json

import requests


class BiboxApi:
    def __init__(self, api_path: str, api_key: str, secret: str):
        assert(isinstance(api_path, str))
        assert(isinstance(api_key, str))
        assert(isinstance(secret, str))

        self.api_path = api_path
        self.api_key = api_key
        self.secret = secret

    def _request(self, path: str, cmd: dict) -> dict:
        assert(isinstance(path, str))
        assert(isinstance(cmd, dict))

        cmds = json.dumps([cmd])
        call = {
            "cmds": cmds,
            "apikey": self.api_key,
            "sign": self._sign(cmds)
        }

        result = requests.post(self.api_path + path, json=call, headers={"Content-Type": "application/json"})
        result_json = result.json()

        if 'error' in result_json:
            raise Exception(f"API error, code {result_json['error']['code']}, msg: '{result_json['error']['msg']}'")

        return result_json['result'][0]['result']

    def _sign(self, msg: str) -> str:
        assert(isinstance(msg, str))
        return hmac.new(key=self.secret.encode('utf-8'), msg=msg.encode('utf-8'), digestmod=hashlib.md5).hexdigest()

    def user_info(self) -> dict:
        return self._request('/v1/user', {"cmd":"user/userInfo","body":{}})
