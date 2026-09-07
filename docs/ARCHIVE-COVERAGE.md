# Internet Archive coverage of this registry

**Measured 2026-09-07** against all 156 registered sources,
by querying the Internet Archive's CDX index once per source URL.

This exists to answer one question before #78 is built: does the Archive
actually hold what a second-witness feature would need? Regenerate with
`python3 tools/measure_archive_coverage.py`.

## The number that is not a number

A query that failed is recorded as `query_failed`, never as zero captures.
That is not a hypothetical distinction. The first collection run used five
concurrent workers and the Archive refused **136 of 156** connections; folded
into a count, that run would have reported an archive holding almost nothing.
Counts are `null` under `query_failed`, so no average can absorb an outage as
a zero.

## What the index holds

| Outcome | Sources |
| --- | ---: |
| `captured` — the index answered with rows | 152 |
| `no_capture` — the index answered with none | 4 |
| `query_failed` — the index could not be read | 0 |

**0** source(s) have captures of which *none*
is an HTTP `200`. Those are the dangerous ones for #78 and they are counted
separately from `no_capture` on purpose: the Archive stores what it received,
403 challenge pages included, so "nothing was ever archived" and "everything
archived is a bot wall" are different facts about a page, and a hash compared
against an archived refusal would report a confident disagreement where no
second witness exists at all.

**32** source(s) reached the 2000-row query limit,
so their counts below are lower bounds and are shown with `≥`.

## The cross-tab #78 turns on

Twelve registry sources cannot be fetched by this tool's own crawler. They are
where a second witness is worth the most, and where a crawler-hostile posture
makes archive coverage least predictable.

| | Sources |
| --- | ---: |
| Unfetchable by our crawler | 12 |
| …of which the Archive has usable captures for | 12 |
| …of which the Archive has none | 0 |
| …of which the index could not be read | 0 |

## The finding #78's design assumes away

`distinct digests / usable captures` says how often the Archive's **raw** bytes
actually differ between captures. Over the
142 source(s) with at least 10
usable captures:

| | value |
| --- | ---: |
| median churn ratio | **0.76** |
| sources at or above 0.9 | **51** |

A ratio near 1.0 means the raw bytes differ on essentially **every** capture. A
witness that compared an archive capture's raw hash against ours would therefore
report `disagrees` almost always, on most sources — a second witness that always
disagrees is noise, and noise a reviewer learns to ignore is worse than no witness.

This does not sink #78; it names which part of it is load-bearing. The proposal
already says captures are *normalized under the same contract version* before
comparison. That sentence is not an optimisation — it is the feature. The numbers
above are raw-byte churn, and this repository's normalizer exists precisely to
strip the session tickers and live dates that produce it (guardrail 7). What is
**not** measured here is the churn that survives normalization, and that is the
number that decides whether `witness` is usable. Measuring it needs the capture
bodies, not the index — a much heavier fetch than this, and the right next step
before the store migration.

## Per source

`usable` counts only HTTP 200 captures. `digests` is the number of distinct
content hashes among them — a floor on how many times the Archive saw the page
change, and the closest thing to the history `docs/THRESHOLD-EVIDENCE.md`
records as missing.

| Source | Ours? | Outcome | Total | Usable | Digests | First | Last |
| --- | :---: | --- | ---: | ---: | ---: | --- | --- |
| `ak-akleg-as-28-15-drivers-license` | yes | `no_capture` | 0 | 0 | 0 | — | — |
| `ak-courts-self-help` | yes | `captured` | 34 | 20 | 11 | 2016-05-28 | 2026-08-14 |
| `ak-havrs-vital-records` | yes | `captured` | 98 | 98 | 94 | 2025-04-02 | 2026-07-26 |
| `al-adph-vital-records` | yes | `captured` | 425 | 186 | 44 | 2017-06-30 | 2026-07-26 |
| `al-alea-driver-license` | yes | `captured` | 215 | 150 | 143 | 2019-01-11 | 2026-08-13 |
| `al-judicial-system` | yes | `captured` | ≥2000 | ≥472 | ≥201 | 2007-07-01 | 2023-08-03 |
| `ar-adh-certificates-records` | yes | `captured` | 46 | 42 | 38 | 2024-09-19 | 2026-08-13 |
| `ar-dfa-mydmv` | yes | `captured` | 123 | 120 | 117 | 2025-01-31 | 2026-09-04 |
| `az-adhs-vital-records` | yes | `captured` | 1258 | 412 | 307 | 2015-09-05 | 2026-05-25 |
| `az-adot-mvd` | yes | `captured` | 65 | 60 | 53 | 2023-05-18 | 2026-06-20 |
| `az-courts-self-service` | yes | `captured` | 450 | 263 | 262 | 2015-08-22 | 2025-10-12 |
| `ca-cdph-vital-records` | **no** | `captured` | 1103 | 1090 | 1076 | 2017-10-21 | 2026-08-12 |
| `ca-court-gender-recognition` | yes | `captured` | 139 | 134 | 127 | 2021-12-27 | 2026-08-27 |
| `ca-court-name-change` | yes | `captured` | 270 | 263 | 242 | 2021-10-19 | 2026-08-14 |
| `ca-dmv-update-dl-id` | yes | `captured` | 215 | 207 | 204 | 2020-06-13 | 2026-08-12 |
| `co-judicial-name-change-adult` | yes | `captured` | 7 | 7 | 6 | 2024-07-18 | 2026-04-17 |
| `ct-dmv` | yes | `captured` | ≥2000 | ≥1502 | ≥1383 | 2015-08-01 | 2022-07-01 |
| `ct-dph-vital-records` | yes | `captured` | 20 | 17 | 17 | 2025-10-03 | 2026-08-21 |
| `dc-code-16-2501-name-change` | yes | `captured` | 3 | 3 | 1 | 2025-02-15 | 2026-01-09 |
| `dc-dmv` | yes | `captured` | ≥2000 | ≥1327 | ≥1012 | 2000-09-29 | 2025-06-28 |
| `dc-vital-records` | yes | `captured` | 41 | 39 | 39 | 2018-07-04 | 2026-01-23 |
| `de-code-16-31-vital-statistics` | yes | `captured` | 82 | 36 | 12 | 2021-02-21 | 2025-06-27 |
| `de-courts` | yes | `captured` | ≥2000 | ≥1332 | ≥597 | 2005-07-15 | 2018-03-25 |
| `de-dmv` | yes | `captured` | ≥2000 | ≥1290 | ≥421 | 2003-09-29 | 2019-08-22 |
| `fl-doh-birth-certificates` | yes | `captured` | 29 | 29 | 29 | 2026-01-07 | 2026-09-03 |
| `fl-hsmv-driver-licenses` | yes | `captured` | 166 | 156 | 131 | 2018-01-04 | 2026-08-26 |
| `ga-courts` | yes | `captured` | 1554 | 1417 | 1079 | 2009-09-05 | 2026-08-31 |
| `ga-dds` | yes | `captured` | 1790 | 1267 | 843 | 2017-02-24 | 2026-08-31 |
| `ga-dph-vital-records` | yes | `captured` | ≥2000 | ≥1567 | ≥949 | 2013-09-11 | 2022-09-25 |
| `hi-courts` | yes | `captured` | ≥2000 | ≥864 | ≥829 | 2002-06-01 | 2023-04-03 |
| `hi-doh-vital-records` | yes | `captured` | 613 | 363 | 282 | 2013-06-24 | 2026-08-13 |
| `hi-hrs-286-102-licensing` | yes | `captured` | 56 | 49 | 19 | 2003-07-13 | 2025-02-08 |
| `ia-courts-representing-yourself` | yes | `captured` | 155 | 130 | 129 | 2018-04-24 | 2026-05-23 |
| `ia-dot-dmv-services` | yes | `captured` | 63 | 62 | 62 | 2025-04-30 | 2026-08-14 |
| `ia-hhs-vital-records` | yes | `captured` | 37 | 35 | 35 | 2025-08-19 | 2026-08-14 |
| `id-code-49-306-license-application` | yes | `captured` | 25 | 17 | 17 | 2017-02-08 | 2025-10-14 |
| `id-court-self-help` | yes | `captured` | 730 | 345 | 134 | 2006-02-12 | 2026-08-14 |
| `id-dhw-change-birth-certificate` | yes | `captured` | 40 | 40 | 36 | 2020-11-29 | 2026-05-21 |
| `il-courts-self-help` | yes | `captured` | ≥2000 | ≥577 | ≥429 | 2013-05-01 | 2024-06-01 |
| `il-idph-vital-records` | yes | `captured` | 235 | 206 | 142 | 2021-10-12 | 2026-08-13 |
| `il-sos-drivers-license` | **no** | `captured` | 70 | 58 | 37 | 2005-05-09 | 2025-05-22 |
| `in-bmv` | yes | `captured` | ≥2000 | ≥1355 | ≥510 | 2001-05-15 | 2018-07-02 |
| `in-courts-self-service` | yes | `captured` | 519 | 130 | 41 | 2021-03-05 | 2026-08-11 |
| `in-doh-vital-records` | yes | `captured` | 670 | 229 | 65 | 2021-06-16 | 2026-08-31 |
| `ks-dov-drivers` | yes | `captured` | 253 | 207 | 30 | 2021-12-11 | 2026-09-06 |
| `ks-kdhe-amend-birth-certificate` | yes | `captured` | 27 | 26 | 26 | 2023-07-22 | 2026-07-06 |
| `ks-kdhe-vital-statistics` | yes | `captured` | 116 | 109 | 109 | 2022-01-19 | 2026-08-14 |
| `ks-statute-60-1401-change-of-name` | yes | `captured` | 4 | 4 | 4 | 2025-03-10 | 2026-04-09 |
| `ky-chfs-vital-statistics` | yes | `captured` | 194 | 127 | 126 | 2018-07-01 | 2026-05-25 |
| `ky-courts` | yes | `captured` | 1113 | 1066 | 1066 | 2021-01-05 | 2026-08-31 |
| `ky-drive` | yes | `captured` | 337 | 331 | 331 | 2023-02-09 | 2026-08-31 |
| `la-ldh-vital-record-amendments` | **no** | `captured` | 28 | 27 | 26 | 2024-03-04 | 2026-07-01 |
| `la-ldh-vital-records` | **no** | `captured` | 28 | 27 | 27 | 2025-03-28 | 2026-06-15 |
| `la-rs-13-4751-petition-for-name-change` | yes | `captured` | 12 | 12 | 9 | 2023-03-06 | 2026-04-09 |
| `la-rs32-ch2-drivers-license` | yes | `captured` | 17 | 17 | 14 | 2016-04-22 | 2025-10-05 |
| `ma-courts-name-changes` | yes | `captured` | 609 | 586 | 556 | 2017-10-17 | 2026-07-10 |
| `ma-rmv` | yes | `captured` | ≥2000 | ≥1106 | ≥835 | 2017-09-14 | 2023-03-09 |
| `ma-rvrs-vital-records` | yes | `captured` | 1327 | 785 | 725 | 2017-09-12 | 2026-07-07 |
| `md-courts` | yes | `captured` | ≥2000 | ≥1007 | ≥894 | 2006-07-05 | 2025-06-24 |
| `md-mva` | yes | `captured` | ≥2000 | ≥960 | ≥537 | 2009-05-01 | 2020-05-03 |
| `md-vsa` | yes | `captured` | 295 | 242 | 209 | 2017-05-16 | 2026-08-13 |
| `me-bmv` | yes | `captured` | ≥2000 | ≥997 | ≥341 | 2003-08-17 | 2025-07-17 |
| `me-cdc-vital-records` | yes | `captured` | 46 | 45 | 23 | 2025-07-25 | 2026-07-26 |
| `me-courts` | yes | `captured` | 1301 | 954 | 342 | 2004-06-20 | 2026-08-31 |
| `mi-mcl-257-307-license-application` | **no** | `captured` | 2 | 2 | 2 | 2025-02-22 | 2025-05-01 |
| `mi-mcl-333-2831-new-birth-certificate` | **no** | `captured` | 3 | 3 | 3 | 2017-01-24 | 2025-08-07 |
| `mn-doh-vital-records` | yes | `captured` | 311 | 144 | 126 | 2020-08-09 | 2026-06-13 |
| `mn-dvs` | yes | `captured` | 671 | 176 | 81 | 2024-11-14 | 2026-08-29 |
| `mn-statute-259-10-change-of-name` | yes | `captured` | 26 | 22 | 16 | 2019-09-20 | 2026-04-09 |
| `mo-courts` | **no** | `captured` | 1287 | 1057 | 670 | 2004-07-11 | 2026-09-03 |
| `mo-dhss-vital-records` | yes | `captured` | 924 | 678 | 558 | 2011-03-25 | 2026-07-08 |
| `mo-dor-driver-license` | yes | `captured` | 297 | 187 | 172 | 2021-07-11 | 2026-08-13 |
| `ms-courts` | yes | `captured` | 1797 | 1244 | 537 | 2011-10-02 | 2026-08-14 |
| `ms-dps-driver-service-bureau` | yes | `captured` | 533 | 379 | 177 | 2018-08-20 | 2026-08-31 |
| `ms-msdh-vital-records` | yes | `captured` | 71 | 54 | 38 | 2022-09-26 | 2026-08-14 |
| `mt-courts-forms` | yes | `captured` | 1255 | 313 | 216 | 2018-01-29 | 2026-07-26 |
| `mt-dphhs-vital-records` | yes | `captured` | 966 | 485 | 306 | 2014-05-28 | 2026-08-14 |
| `mt-mca-61-5-111-drivers-license` | yes | `captured` | 80 | 31 | 8 | 2016-12-31 | 2024-10-05 |
| `nc-dmv` | yes | `captured` | ≥2000 | ≥1316 | ≥152 | 2018-07-12 | 2023-04-08 |
| `nc-gs-101-name-change` | yes | `captured` | 114 | 67 | 10 | 2020-03-24 | 2026-05-13 |
| `nc-vital-records` | yes | `captured` | 834 | 478 | 130 | 2012-03-14 | 2026-08-31 |
| `nd-courts` | yes | `captured` | ≥2000 | ≥1816 | ≥951 | 2007-01-07 | 2024-01-22 |
| `nd-dot-driver` | yes | `captured` | 83 | 74 | 71 | 2024-01-12 | 2026-07-23 |
| `nd-hhs-vital-records` | yes | `captured` | 138 | 131 | 118 | 2022-10-04 | 2026-09-02 |
| `ne-dhhs-vital-records` | yes | `captured` | 857 | 820 | 819 | 2019-04-01 | 2026-09-05 |
| `ne-dmv` | yes | `captured` | 874 | 642 | 506 | 2014-09-25 | 2026-09-03 |
| `ne-judicial` | yes | `captured` | 27 | 24 | 24 | 2025-08-15 | 2026-08-31 |
| `nh-rsa-547-3-i-change-of-name` | yes | `captured` | 7 | 5 | 3 | 2007-06-19 | 2023-03-29 |
| `nh-rsa-5c-87-birth-record-amendment` | yes | `captured` | 33 | 15 | 6 | 2007-06-19 | 2024-11-21 |
| `nh-saf-c-1000-driver-licensing-rules` | yes | `captured` | 17 | 7 | 4 | 2010-03-28 | 2024-09-19 |
| `nj-courts` | yes | `captured` | ≥2000 | ≥564 | ≥382 | 2016-07-11 | 2024-02-03 |
| `nj-doh-vital-statistics` | yes | `captured` | 1187 | 497 | 322 | 2006-03-23 | 2026-09-04 |
| `nj-mvc` | yes | `captured` | ≥2000 | ≥1450 | ≥1300 | 2003-04-12 | 2025-04-15 |
| `nm-courts` | yes | `captured` | ≥2000 | ≥1198 | ≥990 | 2007-02-19 | 2025-06-03 |
| `nm-doh-vital-records` | yes | `captured` | 440 | 242 | 70 | 2014-05-02 | 2026-08-14 |
| `nm-mvd` | yes | `captured` | ≥2000 | ≥636 | ≥550 | 2009-11-20 | 2023-12-10 |
| `nv-courts` | yes | `captured` | ≥2000 | ≥1869 | ≥449 | 2015-03-09 | 2025-03-26 |
| `nv-nac-483-drivers-licenses` | yes | `captured` | 211 | 158 | 41 | 2000-08-16 | 2026-04-09 |
| `nv-nrs-440-vital-statistics` | yes | `captured` | 254 | 183 | 30 | 2000-08-17 | 2026-06-07 |
| `ny-courts-name-change` | yes | `no_capture` | 0 | 0 | 0 | — | — |
| `ny-dmv-change-information` | yes | `captured` | 48 | 47 | 47 | 2024-08-14 | 2026-08-25 |
| `ny-doh-vital-records` | **no** | `captured` | 1014 | 582 | 201 | 2010-09-26 | 2026-07-06 |
| `oh-bmv` | yes | `captured` | ≥2000 | ≥1605 | ≥682 | 2004-02-04 | 2023-07-24 |
| `oh-oac-3701-5-vital-statistics` | yes | `captured` | 19 | 19 | 14 | 2021-05-08 | 2026-06-14 |
| `oh-supreme-court` | yes | `captured` | ≥2000 | ≥978 | ≥713 | 2009-03-13 | 2024-12-13 |
| `ok-service-oklahoma` | yes | `captured` | 554 | 534 | 344 | 2022-11-12 | 2026-08-31 |
| `ok-vital-records` | yes | `captured` | 490 | 476 | 238 | 2022-06-25 | 2026-08-30 |
| `ok-vital-records-forms` | yes | `captured` | 350 | 339 | 170 | 2022-07-07 | 2026-08-13 |
| `or-courts-name-sex-change` | yes | `captured` | 101 | 75 | 75 | 2017-06-20 | 2026-08-05 |
| `or-dmv-gender-marker` | yes | `captured` | 257 | 207 | 205 | 2015-01-09 | 2026-06-10 |
| `or-dmv-name-change` | yes | `captured` | 500 | 441 | 435 | 2012-06-02 | 2026-09-06 |
| `or-oha-vital-records` | yes | `captured` | 890 | 796 | 782 | 2017-06-17 | 2026-09-06 |
| `pa-courts` | yes | `captured` | ≥2000 | ≥1614 | ≥1089 | 2013-01-25 | 2022-08-18 |
| `pa-dmv` | yes | `captured` | 471 | 465 | 423 | 2025-04-04 | 2026-09-01 |
| `pa-doh-vital-records` | yes | `captured` | 54 | 45 | 30 | 2025-07-18 | 2026-08-31 |
| `ri-dmv-licenses` | yes | `captured` | 69 | 61 | 59 | 2021-10-20 | 2026-05-09 |
| `ri-doh-vital-records` | yes | `captured` | 985 | 380 | 154 | 2010-06-05 | 2025-01-08 |
| `ri-gl-33-22-28-name-change` | yes | `captured` | 5 | 4 | 2 | 2023-12-03 | 2025-05-12 |
| `sc-code-15-49-change-of-name` | yes | `captured` | 133 | 93 | 54 | 2012-03-06 | 2026-06-18 |
| `sc-dmv` | yes | `captured` | 121 | 85 | 56 | 2026-01-05 | 2026-08-31 |
| `sc-dph-vital-records` | yes | `captured` | 79 | 71 | 62 | 2024-07-01 | 2026-08-11 |
| `sd-doh-vital-records` | yes | `captured` | 160 | 158 | 56 | 2023-07-26 | 2026-08-31 |
| `sd-sdlrc-32-12-drivers-license` | yes | `captured` | 3 | 3 | 2 | 2024-02-25 | 2025-05-15 |
| `sd-ujs` | yes | `captured` | ≥2000 | ≥1199 | ≥1014 | 2006-08-18 | 2022-12-10 |
| `tn-courts` | yes | `captured` | ≥2000 | ≥991 | ≥877 | 2006-04-27 | 2024-06-12 |
| `tn-doh-vital-records` | yes | `captured` | 67 | 2 | 2 | 2026-06-18 | 2026-07-23 |
| `tn-driver-services` | yes | `captured` | 721 | 689 | 652 | 2018-02-28 | 2026-08-31 |
| `tx-dps-change-dl-id` | yes | `captured` | 176 | 123 | 78 | 2021-03-29 | 2026-08-03 |
| `tx-dshs-vital-statistics` | yes | `captured` | 304 | 293 | 291 | 2022-12-11 | 2026-08-28 |
| `tx-family-code-45-change-of-name` | yes | `captured` | 206 | 174 | 14 | 2018-09-22 | 2026-08-01 |
| `us-federal-register-sex-marker` | yes | `no_capture` | 0 | 0 | 0 | — | — |
| `us-passport-change-correct` | **no** | `captured` | 509 | 411 | 180 | 2019-10-15 | 2026-05-24 |
| `us-passport-sex-markers` | **no** | `captured` | 12 | 10 | 10 | 2026-05-27 | 2026-07-12 |
| `us-selective-service-register` | yes | `captured` | 556 | 463 | 253 | 2020-03-19 | 2026-08-28 |
| `us-ssa-number-card` | **no** | `captured` | 848 | 746 | 713 | 2022-12-06 | 2026-08-31 |
| `us-ssa-ss5-form` | **no** | `captured` | 1636 | 649 | 9 | 2014-08-04 | 2026-08-28 |
| `ut-courts-name-change` | yes | `no_capture` | 0 | 0 | 0 | — | — |
| `ut-dld` | yes | `captured` | 1297 | 772 | 557 | 2015-04-25 | 2026-09-06 |
| `ut-vital-records` | yes | `captured` | 434 | 207 | 182 | 2016-08-24 | 2026-08-31 |
| `va-courts` | yes | `captured` | ≥2000 | ≥1817 | ≥1382 | 2015-07-12 | 2023-12-25 |
| `va-dmv` | yes | `captured` | ≥2000 | ≥1135 | ≥210 | 2004-08-14 | 2022-06-25 |
| `va-vdh-vital-records` | yes | `captured` | ≥2000 | ≥517 | ≥182 | 2016-08-07 | 2020-10-31 |
| `vt-judiciary` | yes | `captured` | 18 | 11 | 11 | 2026-04-19 | 2026-08-31 |
| `vt-vital-records` | yes | `captured` | 639 | 585 | 568 | 2017-01-06 | 2026-06-13 |
| `wa-courts-name-change-form` | yes | `captured` | 232 | 127 | 82 | 2005-12-01 | 2026-05-01 |
| `wa-doh-vital-records` | yes | `captured` | 675 | 599 | 261 | 2022-02-20 | 2026-08-26 |
| `wa-dol-gender-designation` | yes | `captured` | 72 | 69 | 68 | 2023-06-21 | 2026-06-13 |
| `wi-courts` | yes | `captured` | ≥2000 | ≥1266 | ≥599 | 2004-03-30 | 2022-08-28 |
| `wi-dhs-vital-records` | yes | `captured` | 1092 | 852 | 683 | 2008-07-08 | 2026-06-19 |
| `wi-dmv` | yes | `captured` | ≥2000 | ≥1912 | ≥1911 | 2015-07-01 | 2025-11-06 |
| `wv-courts` | yes | `captured` | ≥2000 | ≥1422 | ≥239 | 2011-10-12 | 2023-05-23 |
| `wv-dmv` | yes | `captured` | ≥2000 | ≥1936 | ≥1919 | 2009-10-02 | 2024-10-23 |
| `wv-vital-registration` | yes | `captured` | 29 | 28 | 28 | 2023-03-14 | 2026-05-17 |
| `wy-courts` | yes | `captured` | 275 | 160 | 72 | 2025-05-24 | 2026-08-31 |
| `wy-driver-services` | yes | `captured` | 221 | 168 | 57 | 2017-04-03 | 2026-08-14 |
| `wy-health-vital-records` | yes | `captured` | 809 | 650 | 621 | 2016-07-06 | 2026-08-13 |
