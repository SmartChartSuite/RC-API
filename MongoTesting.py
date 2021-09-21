import pymongo

client = pymongo.MongoClient("mongodb+srv://formsapiuser:454Ik0LIQuOuHSQz@forms.18m6i.mongodb.net/Forms?retryWrites=true&w=majority")
db = client.SmartChartForms

business = {
        'name' : 'Test Business 2',
        'rating' : 2,
        'cuisine' : 'Italian'
    }

result=db.forms.insert_one(business)