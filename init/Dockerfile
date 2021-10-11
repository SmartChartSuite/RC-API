FROM mongo

ARG PROJECT

COPY . .
CMD ls -1 *.json | while read jsonfile; do mongoimport --host mongodb -d ${PROJECT} --drop --file $jsonfile --type json --jsonArray; done