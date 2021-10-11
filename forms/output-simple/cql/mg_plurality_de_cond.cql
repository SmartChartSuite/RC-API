
        library Retrieve2 version '1.0'

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

  


        define "mg_plurality_de_cond_concepts": Concept {
            Code '790111000000101' from "SNOMED", 
            Code '169827000' from "SNOMED", 
            Code '17333005' from "SNOMED", 
            Code '20272009' from "SNOMED", 
            Code '45384004' from "SNOMED", 
            Code '65483000' from "SNOMED", 
            Code '21987001' from "SNOMED", 
            Code '169826009' from "SNOMED"
        }




       define "mg_plurality_de_cond": 
            	 [Condition: Code in "mg_plurality_de_cond_concepts"]


