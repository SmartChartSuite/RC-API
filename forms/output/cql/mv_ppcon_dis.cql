
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

  


        define "mv_ppcon_dis_concepts": Concept {
            Code '21134002' from "SNOMED", 
            Code '123391000119106' from "SNOMED", 
            Code '413298002' from "SNOMED", 
            Code '197521000000108' from "SNOMED", 
            Code '228156007' from "SNOMED", 
            Code '47437004' from "SNOMED", 
            Code '228159000' from "SNOMED", 
            Code '161048004' from "SNOMED", 
            Code '161046000' from "SNOMED", 
            Code '228151002' from "SNOMED", 
            Code '161043008' from "SNOMED", 
            Code '161044002' from "SNOMED", 
            Code '443656000' from "SNOMED", 
            Code '161045001' from "SNOMED", 
            Code '228152009' from "SNOMED", 
            Code '722309000' from "SNOMED", 
            Code '228160005' from "SNOMED", 
            Code '228153004' from "SNOMED", 
            Code '228155006' from "SNOMED", 
            Code '66557003' from "SNOMED", 
            Code '110351007' from "SNOMED", 
            Code '428616007' from "SNOMED", 
            Code '161047009' from "SNOMED", 
            Code '429536005' from "SNOMED", 
            Code '280965007' from "SNOMED", 
            Code '406134008' from "SNOMED", 
            Code '228154005' from "SNOMED", 
            Code '228157003' from "SNOMED", 
            Code '116149007' from "SNOMED", 
            Code '844381000000107' from "SNOMED", 
            Code '239591000000109' from "SNOMED", 
            Code '239631000000109' from "SNOMED", 
            Code '239621000000107' from "SNOMED"
        }




       define "mv_ppcon_dis": 
            	 [Condition: Code in "mv_ppcon_dis_concepts"]


