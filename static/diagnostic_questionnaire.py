"""File to hold diagnostic Questionnaire for repeatable logic running with a gap of 30 seconds between each job "finishing" """

diagnostic_questionnaire = {
    "resourceType": "Questionnaire",
    "id": "diagnostic",
    "extension": [
        {
            "url": "http://gtri.gatech.edu/fakeFormIg/cql-form-job-list",
            "extension": [
                {"url": "form-job", "valueString": "job1.cql"},
                {"url": "form-job", "valueString": "job2.cql"},
                {"url": "form-job", "valueString": "job3.cql"},
                {"url": "form-job", "valueString": "job4.cql"},
                {"url": "form-job", "valueString": "job5.cql"},
                {"url": "form-job", "valueString": "job6.cql"},
                {"url": "form-job", "valueString": "job7.cql"},
                {"url": "form-job", "valueString": "job8.cql"},
                {"url": "form-job", "valueString": "job9.cql"},
                {"url": "form-job", "valueString": "job10.cql"},
                {"url": "form-job", "valueString": "job11.cql"},
                {"url": "form-job", "valueString": "job12.cql"},
                {"url": "form-job", "valueString": "job13.cql"},
                {"url": "form-job", "valueString": "job14.cql"},
                {"url": "form-job", "valueString": "job15.cql"},
                {"url": "form-job", "valueString": "job16.cql"},
                {"url": "form-job", "valueString": "job17.cql"},
                {"url": "form-job", "valueString": "job18.cql"},
                {"url": "form-job", "valueString": "job19.cql"},
                {"url": "form-job", "valueString": "job20.cql"},
            ],
        }
    ],
    "url": "http://gtri.gatech.edu/fakeFormIg/Questionnaire/Diagnostic",
    "version": "1.0",
    "name": "DiagnosticForm",
    "title": "Diagnostic Form",
    "status": "draft",
    "experimental": True,
    "subjectType": ["Patient"],
    "publisher": "GTRI",
    "description": "Package for Stalling Fake Results for UI Testing",
    "item": [
        {
            "linkId": "Section1",
            "text": "Section 1",
            "type": "group",
            "item": [
                {
                    "extension": [{"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"}, {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job1.task1"}],
                    "linkId": "1",
                    "text": "Job 1",
                    "type": "string",
                },
                {
                    "extension": [{"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"}, {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job2.task1"}],
                    "linkId": "2",
                    "text": "Job 2",
                    "type": "string",
                },
                {
                    "extension": [{"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"}, {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job3.task1"}],
                    "linkId": "3",
                    "text": "Job 3",
                    "type": "string",
                },
                {
                    "extension": [{"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"}, {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job4.task1"}],
                    "linkId": "4",
                    "text": "Job 4",
                    "type": "string",
                },
                {
                    "extension": [{"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"}, {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job5.task1"}],
                    "linkId": "5",
                    "text": "Job 5",
                    "type": "string",
                },
                {
                    "extension": [{"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"}, {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job6.task1"}],
                    "linkId": "6",
                    "text": "Job 6",
                    "type": "string",
                },
                {
                    "extension": [{"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"}, {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job7.task1"}],
                    "linkId": "7",
                    "text": "Job 7",
                    "type": "string",
                },
                {
                    "extension": [{"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"}, {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job8.task1"}],
                    "linkId": "8",
                    "text": "Job 8",
                    "type": "string",
                },
                {
                    "extension": [{"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"}, {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job9.task1"}],
                    "linkId": "9",
                    "text": "Job 9",
                    "type": "string",
                },
                {
                    "extension": [
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"},
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job10.task1"},
                    ],
                    "linkId": "10",
                    "text": "Job 10",
                    "type": "string",
                },
                {
                    "extension": [
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"},
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job11.task1"},
                    ],
                    "linkId": "11",
                    "text": "Job 11",
                    "type": "string",
                },
                {
                    "extension": [
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"},
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job12.task1"},
                    ],
                    "linkId": "12",
                    "text": "Job 12",
                    "type": "string",
                },
                {
                    "extension": [
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"},
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job13.task1"},
                    ],
                    "linkId": "13",
                    "text": "Job 13",
                    "type": "string",
                },
                {
                    "extension": [
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"},
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job14.task1"},
                    ],
                    "linkId": "14",
                    "text": "Job 14",
                    "type": "string",
                },
                {
                    "extension": [
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"},
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job15.task1"},
                    ],
                    "linkId": "15",
                    "text": "Job 15",
                    "type": "string",
                },
                {
                    "extension": [
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"},
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job16.task1"},
                    ],
                    "linkId": "16",
                    "text": "Job 16",
                    "type": "string",
                },
                {
                    "extension": [
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"},
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job17.task1"},
                    ],
                    "linkId": "17",
                    "text": "Job 17",
                    "type": "string",
                },
                {
                    "extension": [
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"},
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job18.task1"},
                    ],
                    "linkId": "18",
                    "text": "Job 18",
                    "type": "string",
                },
                {
                    "extension": [
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"},
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job19.task1"},
                    ],
                    "linkId": "19",
                    "text": "Job 19",
                    "type": "string",
                },
                {
                    "extension": [
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cardinality", "valueString": "single"},
                        {"url": "http://gtri.gatech.edu/fakeFormIg/cqlTask", "valueString": "job20.task1"},
                    ],
                    "linkId": "20",
                    "text": "Job 20",
                    "type": "string",
                },
            ],
        }
    ],
}
