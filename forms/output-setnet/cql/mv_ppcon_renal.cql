
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




        define "mv_ppcon_renal_concepts": Concept {
            Code '709044004' from "SNOMED", 
            Code '433144002' from "SNOMED", 
            Code '104931000119100' from "SNOMED", 
            Code '431857002' from "SNOMED", 
            Code '771000119108' from "SNOMED", 
            Code '431856006' from "SNOMED", 
            Code '433146000' from "SNOMED", 
            Code '431855005' from "SNOMED", 
            Code '90688005' from "SNOMED", 
            Code '723190009' from "SNOMED", 
            Code '713313000' from "SNOMED", 
            Code '96441000119101' from "SNOMED", 
            Code '722150000' from "SNOMED", 
            Code '722467000' from "SNOMED", 
            Code '449631000124102' from "SNOMED", 
            Code '722098007' from "SNOMED", 
            Code '722149000' from "SNOMED"
        }




       define "mv_ppcon_renal":
            	 [Condition: Code in "mv_ppcon_renal_concepts"]


