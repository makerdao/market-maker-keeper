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


class BandConfig:
    @staticmethod
    def sample_config(tmpdir):
        file = tmpdir.join("sample_config.json")
        file.write("""{
            "buyBands": [
                {
                    "minMargin": 0.02,
                    "avgMargin": 0.04,
                    "maxMargin": 0.06,
                    "minSaiAmount": 50.0,
                    "avgSaiAmount": 75.0,
                    "maxSaiAmount": 100.0,
                    "dustCutoff": 0.0
                }
            ],
            "sellBands": [
                {
                    "minMargin": 0.02,
                    "avgMargin": 0.04,
                    "maxMargin": 0.06,
                    "minWEthAmount": 5.0,
                    "avgWEthAmount": 7.5,
                    "maxWEthAmount": 10.0,
                    "dustCutoff": 0.0
                }
            ]
        }""")
        return file

    @staticmethod
    def two_adjacent_bands_config(tmpdir):
        file = tmpdir.join("two_adjacent_bands_config.json")
        file.write("""{
            "buyBands": [],
            "sellBands": [
                {
                    "minMargin": 0.02,
                    "avgMargin": 0.04,
                    "maxMargin": 0.06,
                    "minWEthAmount": 5.0,
                    "avgWEthAmount": 7.5,
                    "maxWEthAmount": 8.5,
                    "dustCutoff": 0.0
                },
                {
                    "minMargin": 0.06,
                    "avgMargin": 0.08,
                    "maxMargin": 0.10,
                    "minWEthAmount": 7.0,
                    "avgWEthAmount": 9.5,
                    "maxWEthAmount": 12.0,
                    "dustCutoff": 0.0
                }
            ]
        }""")
        return file

    @staticmethod
    def with_variables_config(tmpdir):
        file = tmpdir.join("with_variables_config.json")
        file.write("""{
            "variables": {
                "avgEthBook": 10
            },
            "buyBands": [],
            "sellBands": [
                {
                    "minMargin": 0.02,
                    "avgMargin": 0.04,
                    "maxMargin": 0.06,
                    "minWEthAmount": $.variables.avgEthBook * 0.25,
                    "avgWEthAmount": $.variables.avgEthBook * 0.5,
                    "maxWEthAmount": $.variables.avgEthBook * 1.0,
                    "dustCutoff": 0.0
                }
            ]
        }""")
        return file

    @staticmethod
    def bands_overlapping_invalid_config(tmpdir):
        file = tmpdir.join("bands_overlapping_invalid_config.json")
        file.write("""{
            "buyBands": [],
            "sellBands": [
                {
                    "minMargin": 0.02,
                    "avgMargin": 0.04,
                    "maxMargin": 0.06,
                    "minWEthAmount": 5.0,
                    "avgWEthAmount": 7.5,
                    "maxWEthAmount": 10.0,
                    "dustCutoff": 0.0
                },
                {
                    "minMargin": 0.059,
                    "avgMargin": 0.07,
                    "maxMargin": 0.08,
                    "minWEthAmount": 5.0,
                    "avgWEthAmount": 7.5,
                    "maxWEthAmount": 10.0,
                    "dustCutoff": 0.0
                }
            ]
        }""")
        return file
