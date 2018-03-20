# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2017-2018 reverendus
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

from market_maker_keeper.feed import Feed, ExpiringFeed, WebSocketFeed, EmptyFeed


def create_spread_feed(arguments) -> Feed:
    if arguments.spread_feed:
        web_socket_feed = WebSocketFeed(arguments.spread_feed, 5)
        expiring_web_socket_feed = ExpiringFeed(web_socket_feed, arguments.spread_feed_expiry)

        return expiring_web_socket_feed
    else:
        return EmptyFeed()
