
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

  


        define "mv_ppcon_immunsup_concepts": Concept {
            Code '234532001' from "SNOMED", 
            Code '402792003' from "SNOMED", 
            Code '33286000' from "SNOMED", 
            Code '88714009' from "SNOMED", 
            Code '36138009' from "SNOMED", 
            Code '191018006' from "SNOMED", 
            Code '58606001' from "SNOMED", 
            Code '402791005' from "SNOMED", 
            Code '24181002' from "SNOMED", 
            Code '9893005' from "SNOMED", 
            Code '735536003' from "SNOMED", 
            Code '707152007' from "SNOMED", 
            Code '737307003' from "SNOMED", 
            Code '48119005' from "SNOMED", 
            Code '442459007' from "SNOMED", 
            Code '702444009' from "SNOMED", 
            Code '417167007' from "SNOMED", 
            Code '234556002' from "SNOMED", 
            Code '703538003' from "SNOMED", 
            Code '234631003' from "SNOMED", 
            Code '703540008' from "SNOMED", 
            Code '234632005' from "SNOMED", 
            Code '24419001' from "SNOMED", 
            Code '234543009' from "SNOMED", 
            Code '711480000' from "SNOMED", 
            Code '234564008' from "SNOMED", 
            Code '783245001' from "SNOMED", 
            Code '721903007' from "SNOMED", 
            Code '234584007' from "SNOMED", 
            Code '234553005' from "SNOMED", 
            Code '768560008' from "SNOMED", 
            Code '773702002' from "SNOMED", 
            Code '773646003' from "SNOMED", 
            Code '784339002' from "SNOMED", 
            Code '773664005' from "SNOMED", 
            Code '773662009' from "SNOMED", 
            Code '234539005' from "SNOMED", 
            Code '234573000' from "SNOMED", 
            Code '818950005' from "SNOMED", 
            Code '770687001' from "SNOMED", 
            Code '449853003' from "SNOMED", 
            Code '770785002' from "SNOMED", 
            Code '778028008' from "SNOMED", 
            Code '778045003' from "SNOMED", 
            Code '771333006' from "SNOMED", 
            Code '778023004' from "SNOMED"
        }




       define "mv_ppcon_immunsup": 
            	 [Condition: Code in "mv_ppcon_immunsup_concepts"]


