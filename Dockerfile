FROM python:3.11-slim

RUN apt-get -y update
RUN apt-get -y install git

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

EXPOSE 8080

COPY . .

CMD ["hypercorn", "main:app", "--bind", "0.0.0.0:8080", "--access-logfile", "-", "--access-logformat", "%(h)s %(l)s \"%(r)s\" %(s)s Origin:\"%({origin}i)s\" X-Forwarded-For:\"%({x-forwarded-for}i)s\""]