import pymongo

client = pymongo.MongoClient("mongodb+srv://formsapiuser:i3lworks@forms.18m6i.mongodb.net/Forms?retryWrites=true&w=majority")
db = client.SmartChartForms

print(client.list_database_names())