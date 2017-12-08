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

import subprocess

from pymaker.numeric import Wad


class Setzer():
    """A client for the `setzer` tool.

    You can find the `setzer` tool here: <https://github.com/makerdao/setzer>.

    `setzer` needs to be installed and its installation folder (like `/usr/local/bin`)
    has to be present in the $PATH variable. Alternatively, you can specify the full
    path to `setzer` as the `command` constructor parameter of this class.

    Attributes:
        command: The full path to the `setzer` tool.
    """

    def __init__(self, command: str = 'setzer'):
        assert(isinstance(command, str))
        self.command = command

    def price(self, source: str) -> Wad:
        """Get the current price from `source` using `setzer`.

        Args:
            source: Name of the source to get the price from. You can list available price sources
                by calling `setzer --help` and looking for the commands starting with `price`.
        """
        assert(isinstance(source, str))

        line = f"{self.command} price {source}"
        process = subprocess.Popen(line.split(), stdout=subprocess.PIPE)
        output, error = process.communicate()
        if error is not None:
            raise ValueError(f'Error invoking setzer via {line}: {error}')

        return Wad.from_number(float(output))

    def volume(self, source: str) -> Wad:
        """Get the current volume from `source` using `setzer`.

        Args:
            source: Name of the source to get the volume from. You can list available volume sources
                by calling `setzer --help` and looking for the commands starting with `volume`.
        """
        assert(isinstance(source, str))

        line = f"{self.command} volume {source}"
        process = subprocess.Popen(line.split(), stdout=subprocess.PIPE)
        output, error = process.communicate()
        if error is not None:
            raise ValueError(f'Error invoking setzer via {line}: {error}')

        return Wad.from_number(float(output))

    def __repr__(self):
        return f"Setzer()"
