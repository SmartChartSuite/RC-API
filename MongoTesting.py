import pymongo

client = pymongo.MongoClient(host='bare.claritynlp.cloud/nlp-mongo', port=27017, username='admin',
                                    password='password', socketTimeoutMS=15000, maxPoolSize=500,
                                    maxIdleTimeMS=30000)

client.list_database_names()