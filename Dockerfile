FROM python:3.14-slim

RUN apt-get -y update && \
    apt-get -y install git libpq-dev gcc

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

EXPOSE 8080

COPY . .

CMD ["hypercorn", "main:app", "--config", "hypercorn_config.toml"]
