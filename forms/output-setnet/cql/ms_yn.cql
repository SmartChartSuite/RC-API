
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




        define "ms_yn_concepts": Concept {
            Code '76272004' from "SNOMED", 
            Code '312934004' from "SNOMED", 
            Code '197966009' from "SNOMED", 
            Code '21523006' from "SNOMED", 
            Code '240569007' from "SNOMED", 
            Code '240567009' from "SNOMED", 
            Code '240563008' from "SNOMED", 
            Code '233849007' from "SNOMED", 
            Code '302813001' from "SNOMED", 
            Code '235064008' from "SNOMED", 
            Code '235032001' from "SNOMED", 
            Code '234017002' from "SNOMED", 
            Code '235065009' from "SNOMED", 
            Code '371237000' from "SNOMED", 
            Code '42770003' from "SNOMED", 
            Code '49923008' from "SNOMED", 
            Code '55768006' from "SNOMED", 
            Code '61612001' from "SNOMED", 
            Code '82355002' from "SNOMED", 
            Code '58056005' from "SNOMED", 
            Code '410470003' from "SNOMED", 
            Code '4483005' from "SNOMED", 
            Code '45058001' from "SNOMED", 
            Code '62207008' from "SNOMED", 
            Code '64102008' from "SNOMED", 
            Code '37430004' from "SNOMED", 
            Code '402940004' from "SNOMED", 
            Code '402942007' from "SNOMED", 
            Code '402941000' from "SNOMED", 
            Code '402943002' from "SNOMED", 
            Code '77782006' from "SNOMED", 
            Code '402950003' from "SNOMED", 
            Code '94851000119107' from "SNOMED", 
            Code '10759921000119107' from "SNOMED", 
            Code '331151000119109' from "SNOMED", 
            Code '711617002' from "SNOMED", 
            Code '50528008' from "SNOMED", 
            Code '88943008' from "SNOMED", 
            Code '59307008' from "SNOMED", 
            Code '186899004' from "SNOMED", 
            Code '86028001' from "SNOMED", 
            Code '34964002' from "SNOMED", 
            Code '186875004' from "SNOMED", 
            Code '186877007' from "SNOMED", 
            Code '186878002' from "SNOMED", 
            Code '20735004' from "SNOMED", 
            Code '278480000' from "SNOMED", 
            Code '3589003' from "SNOMED", 
            Code '12232008' from "SNOMED", 
            Code '38523005' from "SNOMED", 
            Code '36276008' from "SNOMED", 
            Code '26135000' from "SNOMED", 
            Code '22386003' from "SNOMED", 
            Code '19206003' from "SNOMED", 
            Code '67391006' from "SNOMED", 
            Code '76272004' from "SNOMED", 
            Code '34242002' from "SNOMED", 
            Code '44568006' from "SNOMED", 
            Code '77939001' from "SNOMED", 
            Code '301086002' from "SNOMED", 
            Code '59530001' from "SNOMED", 
            Code '4082005' from "SNOMED", 
            Code '11338007' from "SNOMED", 
            Code '8555001' from "SNOMED", 
            Code '202933002' from "SNOMED", 
            Code '237446005' from "SNOMED", 
            Code '230735006' from "SNOMED", 
            Code '23550005' from "SNOMED", 
            Code '16070004' from "SNOMED", 
            Code '197305002' from "SNOMED"
        }




       define "ms_yn":
            	 [Condition: Code in "ms_yn_concepts"]


