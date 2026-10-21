# Credits for the demo film (`film/out/receipts-demo.mp4`)

## Photography

Three photographs set the scene where the narration is not pointing at the app (hook, first and third lines; the opening of the "fix" scene). The app footage is the real app and is never covered by a photograph while it is being discussed. Each picture is graded to the film's paper palette (`film/tools/grade_photos.py`: lower saturation, a gentle contrast, warm highlights, lifted blacks, a soft vignette), moved only by a slow eased push, and kept clear of the captions. No faces, logos or signage are in frame.

The files in `film/assets/` are the 3840 px-wide versions served by Wikimedia Commons (the full-size originals are 6144 to 7230 px wide); all are under 5 MB, so they are tracked. The graded copies are generated into `film/work/photos/` (git-ignored).

| Photograph | Author | Source page | Licence | Where it is used |
| --- | --- | --- | --- | --- |
| Main Reading Room of the Library of Congress, Thomas Jefferson Building (`assets/loc-main-reading-room-highsmith.jpg`, 3840 × 2561, 2.7 MB) | Carol M. Highsmith (Carol M. Highsmith Archive, Library of Congress Prints and Photographs Division, digital ID highsm.11604) | https://commons.wikimedia.org/wiki/File:LOC_Main_Reading_Room_Highsmith.jpg | Public domain (Commons: not copyrighted; the Highsmith Archive was given to the Library of Congress and the public) | Hook, first line: slow push-in behind five floating citation markers, 0:00 to 0:05 |
| Columns, Byron R. White U.S. Courthouse, Denver, Colorado (`assets/byron-white-courthouse-columns-highsmith.jpg`, 3840 × 2491, 2.0 MB) | Carol M. Highsmith (taken 9 July 2008; LCCN 2010719071, hdl.loc.gov/loc.pnp/highsm.03515) | https://commons.wikimedia.org/wiki/File:Columns,_Byron_R._White_U.S._Courthouse,_Denver,_Colorado_LCCN2010719071.tif | Public domain (as above) | Hook, third line: darkened and softened behind the 2,202 statistic and its source line, 0:08 to 0:16 |
| Library of Congress Serials Card Catalog, John Adams Building, 23 January 2025 (`assets/loc-serials-card-catalog-2025.jpg`, 3840 × 2561, 1.3 MB) | Shawn Miller / Library of Congress (credited on Commons as "Library of Congress Life") | https://commons.wikimedia.org/wiki/File:Library_of_Congress_Serials_Card_Catalog_in_the_John_Adams_Building_on_23_January_2025.jpg (original on Flickr: https://www.flickr.com/photos/162991176@N03/54336888422/) | CC0 1.0 (http://creativecommons.org/publicdomain/zero/1.0/) | Fix scene, first line: 3-second push, then a dissolve into the app. The catalogue card and the drawer label are blurred locally in the grading step so that their text does not compete with the film's captions |

Licences were read from each file's Commons metadata (`LicenseShortName`, `Copyrighted`) before the download. Files were fetched from commons.wikimedia.org / thumb.wikimedia.org only.

## Everything else

- Narration: Kokoro-82M (Apache-2.0), voice `af_heart`. Music and sound effects are synthesised in `film/assemble.py` (no samples).
- Fonts: Source Serif 4 and Inter (SIL Open Font License 1.1), loaded from Google Fonts.
- The app scenes show the real Receipts web app (MIT) with its recorded examples and one recorded real run (`film/data/live-run.jsonl`).
- Statistic in the opening: AI Hallucination Cases database, damiencharlotin.com/hallucinations (2,202 decisions as of 19 October 2026), cited as a source line only.
