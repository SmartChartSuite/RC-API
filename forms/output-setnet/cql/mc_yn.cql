
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




        define "mc_yn_concepts": Concept {
            Code '507101007' from "SNOMED", 
            Code '708198006' from "SNOMED", 
            Code '703866000' from "SNOMED", 
            Code '347891000119103' from "SNOMED", 
            Code '128971000119101' from "SNOMED", 
            Code '120241000119100' from "SNOMED", 
            Code '824901000000104' from "SNOMED", 
            Code '824891000000100' from "SNOMED", 
            Code '824881000000102' from "SNOMED", 
            Code '824871000000104' from "SNOMED", 
            Code '824851000000108' from "SNOMED", 
            Code '824841000000105' from "SNOMED", 
            Code '761381000000102' from "SNOMED", 
            Code '831000119103' from "SNOMED", 
            Code '200371000000104' from "SNOMED", 
            Code '235872006' from "SNOMED", 
            Code '988111000000106' from "SNOMED", 
            Code '146371000119104' from "SNOMED", 
            Code '125921000119106' from "SNOMED", 
            Code '702969000' from "SNOMED", 
            Code '199203001' from "SNOMED", 
            Code '278929008' from "SNOMED", 
            Code '170489004' from "SNOMED", 
            Code '442374005' from "SNOMED", 
            Code '186628001' from "SNOMED", 
            Code '128302006' from "SNOMED", 
            Code '50711007' from "SNOMED", 
            Code '235866006' from "SNOMED"
        }




       define "mc_yn":
            	 [Condition: Code in "mc_yn_concepts"]


