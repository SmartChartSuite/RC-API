
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

  


        define "rbc_structured_concepts": Concept {
            Code '789-8' from "LOINC"
        }




       define "rbc_structured": 
            	 [Observation: Code in "rbc_structured_concepts"]


