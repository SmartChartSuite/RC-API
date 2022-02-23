
        library temporary_retrieve version '1.0'

        using FHIR version '3.0.0'

        include FHIRHelpers version '3.0.0' called FHIRHelpers

        codesystem "LOINC": 'http://loinc.org'
        codesystem "SNOMED": '2.16.840.1.113883.6.96'
        codesystem "RxNorm": 'http://www.nlm.nih.gov/research/umls/rxnorm'
        codesystem "CPT": 'http://www.ama-assn.org/go/cpt'
        codesystem "ICD9": '2.16.840.1.113883.6.42'
        codesystem "ICD10": '2.16.840.1.113883.6.3'
        codesystem USCoreEthnicitySystem: '2.16.840.1.113883.6.238'
        codesystem RelationshipType: '2.16.840.1.113883.4.642.3.449'


        

        context Patient




        define "mg_death_concepts": Concept {
            Code '237360006' from "SNOMED", 
            Code '27792001' from "SNOMED", 
            Code '288260005' from "SNOMED", 
            Code '31868001' from "SNOMED", 
            Code '370912008' from "SNOMED", 
            Code '5866003' from "SNOMED", 
            Code '59283008' from "SNOMED", 
            Code '698714004' from "SNOMED", 
            Code '237358009' from "SNOMED", 
            Code '698715003' from "SNOMED"
        }




       define "mg_death":
            	 [Observation: Code in "mg_death_concepts"]


