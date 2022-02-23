
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




        define "mv_ppcon_psych_concepts": Concept {
            Code '116367006' from "SNOMED", 
            Code '445651000124107' from "SNOMED", 
            Code '22058002' from "SNOMED", 
            Code '191737008' from "SNOMED", 
            Code '106022001' from "SNOMED", 
            Code '12200008' from "SNOMED", 
            Code '64270008' from "SNOMED", 
            Code '704488001' from "SNOMED", 
            Code '106023006' from "SNOMED", 
            Code '2421000119107' from "SNOMED", 
            Code '15625571000119100' from "SNOMED", 
            Code '6811007' from "SNOMED", 
            Code '723926008' from "SNOMED", 
            Code '106122003' from "SNOMED", 
            Code '397706001' from "SNOMED", 
            Code '71529007' from "SNOMED", 
            Code '53686002' from "SNOMED", 
            Code '733819000' from "SNOMED", 
            Code '110351007' from "SNOMED", 
            Code '871840004' from "SNOMED", 
            Code '37448008' from "SNOMED", 
            Code '81659004' from "SNOMED", 
            Code '736320009' from "SNOMED", 
            Code '5921000175108' from "SNOMED", 
            Code '5988002' from "SNOMED", 
            Code '724666003' from "SNOMED", 
            Code '724667007' from "SNOMED", 
            Code '724665004' from "SNOMED", 
            Code '724755002' from "SNOMED", 
            Code '724753009' from "SNOMED", 
            Code '724726005' from "SNOMED", 
            Code '5461000175107' from "SNOMED", 
            Code '5451000175105' from "SNOMED", 
            Code '519261000000101' from "SNOMED", 
            Code '118191004' from "SNOMED", 
            Code '365922003' from "SNOMED", 
            Code '762620006' from "SNOMED", 
            Code '762622003' from "SNOMED"
        }




       define "mv_ppcon_psych":
            	 [Condition: Code in "mv_ppcon_psych_concepts"]


