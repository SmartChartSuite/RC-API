
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

  


        define "mv_ppcon_cardio_concepts": Concept {
            Code '49601007' from "SNOMED", 
            Code '400047006' from "SNOMED", 
            Code '38341003' from "SNOMED", 
            Code '45007003' from "SNOMED", 
            Code '56265001' from "SNOMED", 
            Code '62914000' from "SNOMED", 
            Code '55855009' from "SNOMED", 
            Code '128487001' from "SNOMED", 
            Code '432119003' from "SNOMED", 
            Code '10596002' from "SNOMED", 
            Code '427461000' from "SNOMED", 
            Code '282728007' from "SNOMED", 
            Code '276510003' from "SNOMED", 
            Code '27550009' from "SNOMED", 
            Code '473459007' from "SNOMED", 
            Code '724497009' from "SNOMED", 
            Code '439127006' from "SNOMED", 
            Code '4249000' from "SNOMED", 
            Code '213217008' from "SNOMED", 
            Code '361131008' from "SNOMED", 
            Code '443089001' from "SNOMED", 
            Code '128998007' from "SNOMED", 
            Code '363005004' from "SNOMED", 
            Code '363696006' from "SNOMED", 
            Code '432995009' from "SNOMED", 
            Code '400171002' from "SNOMED", 
            Code '128292002' from "SNOMED", 
            Code '762228008' from "SNOMED", 
            Code '126531000119109' from "SNOMED", 
            Code '105980002' from "SNOMED", 
            Code '459157005' from "SNOMED", 
            Code '459156001' from "SNOMED", 
            Code '461338005' from "SNOMED"
        }




       define "mv_ppcon_cardio": 
            	 [Condition: Code in "mv_ppcon_cardio_concepts"]


