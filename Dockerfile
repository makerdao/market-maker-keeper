FROM python:3.6.6

RUN groupadd -r maker && useradd --no-log-init -r -g maker maker

WORKDIR /opt/maker

COPY bin /opt/maker/market-maker-keeper/bin
COPY lib /opt/maker/market-maker-keeper/lib
COPY market_maker_keeper /opt/maker/market-maker-keeper/market_maker_keeper
COPY install.sh /opt/maker/market-maker-keeper/install.sh
COPY requirements.txt /opt/maker/market-maker-keeper/requirements.txt

WORKDIR /opt/maker/market-maker-keeper
RUN pip3 install virtualenv
RUN ./install.sh
WORKDIR /opt/maker/market-maker-keeper/bin

USER maker