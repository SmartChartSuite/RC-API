
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




        define "mv_ppcon_liver_concepts": Concept {
            Code '328383001' from "SNOMED", 
            Code '79720007' from "SNOMED", 
            Code '19943007' from "SNOMED", 
            Code '76783007' from "SNOMED", 
            Code '34736002' from "SNOMED", 
            Code '713181003' from "SNOMED", 
            Code '197286002' from "SNOMED", 
            Code '432908002' from "SNOMED", 
            Code '31155007' from "SNOMED", 
            Code '73475009' from "SNOMED", 
            Code '58282009' from "SNOMED", 
            Code '314963000' from "SNOMED", 
            Code '863957008' from "SNOMED", 
            Code '735451005' from "SNOMED"
        }




       define "mv_ppcon_liver":
            	 [Condition: Code in "mv_ppcon_liver_concepts"]


