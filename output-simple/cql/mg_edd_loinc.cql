
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

  


        define "mg_edd_loinc_concepts": Concept {
            Code '11778-8' from "LOINC", 
            Code '53691-0' from "LOINC", 
            Code '11780-4' from "LOINC", 
            Code '57063-0' from "LOINC", 
            Code '90368-2' from "LOINC", 
            Code '11779-6' from "LOINC", 
            Code '57064-8' from "LOINC", 
            Code '53694-6' from "LOINC", 
            Code '11781-2' from "LOINC"
        }




       define "mg_edd_loinc": 
            	 [Observation: Code in "mg_edd_loinc_concepts"]


