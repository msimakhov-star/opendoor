# rule_off vs rule_on_v3

advocate.py identical across every call: **True** (sha256 `0bd552801e0557d4`)  
Same verdicts in the same order: **True**  
rule_off at 2026-09-19T13:37:18+00:00, rule_on_v3 at 2026-09-19T13:55:23+00:00

## note (5 vs 5 ok runs)

| metric | rule_off | rule_on_v3 | change |
| --- | --- | --- | --- |
| output_tokens | 146 [131 to 155] | 88 [78 to 96] | -58 (-39.7%) |
| input_tokens | 179 [169 to 182] | 478 [468 to 481] | +299 (+167.0%) |
| latency_s | 11.192 [9.513 to 17.209] | 10.991 [8.655 to 13.18] | -0.201 (-1.8%) |
| word_count | 126 [98 to 131] | 75 [69 to 85] | -51 (-40.5%) |
| sentence_count | 10 [9 to 11] | 7 [6 to 7] | -3 (-30.0%) |
| flesch_reading_ease | 59.4 [42.8 to 63.2] | 68.9 [66.1 to 69.6] | +9.5 (+16.0%) |
| flesch_kincaid_grade | 7.9 [7.6 to 10.1] | 6.5 [6.2 to 7.1] | -1.4 (-17.7%) |
| banned_total | 0 [0 to 0] | 0 [0 to 0] | +0 (n/a) |
| has_nhs_url | 0 of 5 | 5 of 5 | +5 (n/a) |
| contains_quote | 0 of 5 | 0 of 5 | +0 (n/a) |
| contains_nhs_quote | 0 of 5 | 0 of 5 | +0 (n/a) |
| placeholders | 3 [3 to 5] | 0 [0 to 0] | -3 (-100.0%) |

Most different note pair: run 3, Example Surgery C

| rule_off (trace `01a0b9e0ffa8faf21e852b369cec1eaa`) | rule_on_v3 (trace `01a0b9f213e99d882a70e9f0970f01a2`) |
| --- | --- |
| Dear [Patient's Name],<br><br>We have noticed that the registration process for Example Surgery C, as described on their website, asks for photo ID and proof of address (such as a utility bill or bank statement), which is not strictly required according to the latest NHS guidance. The NHS recommends that you do not need to provide ID, proof of address, or proof of immigration status when registering with a GP surgery.<br><br>If you have any concerns or questions about this, please feel free to contact our practice directly. We are here to assist you and ensure that the registration process is as smooth and straightforward as possible.<br><br>Best regards,<br><br>[Your Name]  <br>[Your Position]  <br>Example Surgery C | **Note for a Patient:**<br><br>The NHS guidance on nhs.uk says you do not need to bring ID, proof of address, or proof of immigration status when you register with a GP surgery. However, the practice website for Example Surgery C suggests bringing photo ID and a recent utility bill or bank statement to help match your medical records.<br><br>You can show the NHS guidance link at reception if you have any questions.<br><br>Open Door |

## letter (5 vs 5 ok runs)

| metric | rule_off | rule_on_v3 | change |
| --- | --- | --- | --- |
| output_tokens | 367 [310 to 387] | 164 [150 to 213] | -203 (-55.3%) |
| input_tokens | 180 [170 to 183] | 479 [469 to 482] | +299 (+166.1%) |
| latency_s | 30.565 [22.139 to 45.604] | 14.277 [11.685 to 23.06] | -16.288 (-53.3%) |
| word_count | 270 [257 to 301] | 143 [131 to 186] | -127 (-47.0%) |
| sentence_count | 19 [19 to 22] | 13 [12 to 15] | -6 (-31.6%) |
| flesch_reading_ease | 53.2 [47 to 61.4] | 64.3 [62.2 to 70.2] | +11.1 (+20.9%) |
| flesch_kincaid_grade | 8.9 [8.2 to 10.6] | 7 [6.2 to 7.3] | -1.9 (-21.3%) |
| banned_total | 0 [0 to 0] | 0 [0 to 0] | +0 (n/a) |
| has_nhs_url | 4 of 5 | 5 of 5 | +1 (+25.0%) |
| contains_quote | 5 of 5 | 5 of 5 | +0 (+0.0%) |
| contains_nhs_quote | 5 of 5 | 5 of 5 | +0 (+0.0%) |
| placeholders | 5 [5 to 5] | 0 [0 to 0] | -5 (-100.0%) |

Most different letter pair: run 5, Example Surgery A

| rule_off (trace `01a0b9e25b5c5cdab9dfa4b2b4ec2344`) | rule_on_v3 (trace `01a0b9f31b1e3b8221d82a772af21113`) |
| --- | --- |
| Subject: Website Compliance and Inconsistency with NHS Guidance<br><br>Dear [Practice Manager's Name],<br><br>I hope this message finds you well. I recently conducted an automated review of the website for Example Surgery A, and I noticed a discrepancy between the practice’s website and the official NHS guidance regarding the documentation required for new patient registrations.<br><br>The page in question, "https://example-surgery-a.example.org/new-patients/", states that "To register with the practice you must bring photographic ID (passport or driving licence) and proof of address dated within the last three months. We are unable to process your registration without these documents." However, the official NHS guidance, available at https://www.nhs.uk/nhs-services/gps/how-to-register-with-a-gp-surgery/, clearly states that "You do not need ID, proof of address or proof of immigration status."<br><br>This inconsistency could potentially cause confusion for new patients and may not align with the current NHS standards. It is important that our practice adheres to the official guidance to ensure that all patients are treated fairly and without unnecessary barriers.<br><br>I recommend that we update the relevant page on our website to reflect the current NHS guidance. The updated sentence could be:<br><br>"New patients do not need to bring any documents to register with our practice. We will gather any necessary information during your first appointment."<br><br>Please let me know if you need any assistance in making this update or if you have any questions.<br><br>Thank you for your attention to this matter.<br><br>Best regards,<br><br>[Your Full Name]  <br>[Your Position]  <br>[Your Contact Information]  <br>[Your Email Address]  <br><br>---<br><br>Feel free to adjust any details as necessary to fit your specific situation. | Dear Example Surgery A,<br><br>We noticed a discrepancy between your practice website and the NHS guidance on nhs.uk regarding the requirements for new patients to register.<br><br>Your practice website states: "To register with the practice you must bring photographic ID (passport or driving licence) and proof of address dated within the last three months. We are unable to process your registration without these documents."<br><br>However, the NHS guidance on nhs.uk says: "You do not need ID, proof of address or proof of immigration status."<br><br>The NHS guidance on nhs.uk suggests that new patients can register without needing to bring any documents.<br><br>We recommend that you update your practice website to reflect the NHS guidance. This will help ensure that patients are aware of the correct requirements and can register more easily.<br><br>Thank you for your attention to this matter.<br><br>Open Door |
