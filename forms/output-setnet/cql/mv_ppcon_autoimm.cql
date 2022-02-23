
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




        define "mv_ppcon_autoimm_concepts": Concept {
            Code '85828009' from "SNOMED", 
            Code '69896004' from "SNOMED", 
            Code '83901003' from "SNOMED", 
            Code '91637004' from "SNOMED", 
            Code '200936003' from "SNOMED", 
            Code '413603009' from "SNOMED", 
            Code '26843008' from "SNOMED", 
            Code '40956001' from "SNOMED", 
            Code '36715001' from "SNOMED", 
            Code '193497004' from "SNOMED", 
            Code '56989000' from "SNOMED", 
            Code '128091003' from "SNOMED", 
            Code '235890007' from "SNOMED", 
            Code '446682003' from "SNOMED", 
            Code '427213005' from "SNOMED", 
            Code '445187004' from "SNOMED", 
            Code '95643007' from "SNOMED", 
            Code '713654004' from "SNOMED", 
            Code '232308006' from "SNOMED", 
            Code '95329006' from "SNOMED", 
            Code '235728001' from "SNOMED", 
            Code '702575003' from "SNOMED", 
            Code '722991004' from "SNOMED", 
            Code '10743271000119100' from "SNOMED", 
            Code '123777002' from "SNOMED", 
            Code '737249005' from "SNOMED", 
            Code '723022008' from "SNOMED", 
            Code '276575001' from "SNOMED", 
            Code '1073891000119100' from "SNOMED", 
            Code '1073881000119100' from "SNOMED", 
            Code '724809006' from "SNOMED", 
            Code '1073871000119100' from "SNOMED", 
            Code '722386009' from "SNOMED", 
            Code '443899007' from "SNOMED", 
            Code '237822008' from "SNOMED", 
            Code '402402005' from "SNOMED", 
            Code '428470000' from "SNOMED", 
            Code '723004005' from "SNOMED", 
            Code '362992004' from "SNOMED", 
            Code '788613004' from "SNOMED", 
            Code '1073861000119100' from "SNOMED", 
            Code '783787000' from "SNOMED", 
            Code '778004006' from "SNOMED", 
            Code '721093000' from "SNOMED", 
            Code '723508002' from "SNOMED", 
            Code '778023004' from "SNOMED", 
            Code '707443007' from "SNOMED", 
            Code '16208721000119100' from "SNOMED", 
            Code '16208561000119100' from "SNOMED", 
            Code '838330008' from "SNOMED", 
            Code '771333006' from "SNOMED", 
            Code '838324008' from "SNOMED", 
            Code '239791005' from "SNOMED", 
            Code '239792003' from "SNOMED", 
            Code '287006005' from "SNOMED", 
            Code '287007001' from "SNOMED", 
            Code '201777003' from "SNOMED", 
            Code '287008006' from "SNOMED", 
            Code '201771002' from "SNOMED", 
            Code '201766009' from "SNOMED", 
            Code '57160007' from "SNOMED", 
            Code '201775006' from "SNOMED", 
            Code '201769002' from "SNOMED", 
            Code '399923009' from "SNOMED", 
            Code '201791009' from "SNOMED", 
            Code '319841000119107' from "SNOMED", 
            Code '28880005' from "SNOMED", 
            Code '201770001' from "SNOMED", 
            Code '398640008' from "SNOMED", 
            Code '86219005' from "SNOMED", 
            Code '201767000' from "SNOMED", 
            Code '201768005' from "SNOMED", 
            Code '52661003' from "SNOMED", 
            Code '201778008' from "SNOMED", 
            Code '735600005' from "SNOMED", 
            Code '239795001' from "SNOMED", 
            Code '735599007' from "SNOMED", 
            Code '143441000119108' from "SNOMED", 
            Code '201776007' from "SNOMED", 
            Code '781206002' from "SNOMED", 
            Code '427770001' from "SNOMED", 
            Code '15691761000119100' from "SNOMED", 
            Code '15687841000119100' from "SNOMED", 
            Code '15686001000119100' from "SNOMED", 
            Code '15685961000119100' from "SNOMED", 
            Code '459911000124100' from "SNOMED", 
            Code '1073771000119100' from "SNOMED", 
            Code '1073761000119100' from "SNOMED", 
            Code '1073691000119100' from "SNOMED", 
            Code '1073681000119100' from "SNOMED", 
            Code '788613004' from "SNOMED", 
            Code '1073861000119100' from "SNOMED", 
            Code '783787000' from "SNOMED", 
            Code '778004006' from "SNOMED", 
            Code '721093000' from "SNOMED", 
            Code '723508002' from "SNOMED", 
            Code '778023004' from "SNOMED", 
            Code '707443007' from "SNOMED", 
            Code '16208721000119100' from "SNOMED", 
            Code '16208561000119100' from "SNOMED", 
            Code '838330008' from "SNOMED", 
            Code '771333006' from "SNOMED", 
            Code '838324008' from "SNOMED"
        }




       define "mv_ppcon_autoimm":
            	 [Condition: Code in "mv_ppcon_autoimm_concepts"]


