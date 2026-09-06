import assert from "node:assert/strict";
import test from "node:test";

import { prepareSourceDirectory, sourceLibraryOrder } from "../src/mentor/static/source_import.js";

const file = (path) => ({ name: path.split("/").at(-1), webkitRelativePath: path });

test("mixed selected folder keeps valid transcripts and ignores support files", () => {
  const selected = [
    file("GxT-Transcripts/Garrett/Advanced/Lesson.txt"),
    file("GxT-Transcripts/Afyz/Q&A/Test.TXT"),
    file("GxT-Transcripts/Erik/Youtube/Test.txt"),
    file("GxT-Transcripts/Splash/Test.txt"),
    file("GxT-Transcripts/Zay/Test.txt"),
    file("GxT-Transcripts/.gxt-transcription-manifest.json"),
    file("GxT-Transcripts/import.log"),
  ];

  const prepared = prepareSourceDirectory(selected);

  assert.equal(prepared.files.length, 5);
  assert.equal(prepared.ignoredCount, 2);
  assert.deepEqual(
    prepared.files.map((item) => item.name),
    ["Lesson.txt", "Test.TXT", "Test.txt", "Test.txt", "Test.txt"],
  );
});

test("zero transcripts and wrong nested selection are actionable", () => {
  assert.throws(
    () => prepareSourceDirectory([file("GxT-Transcripts/manifest.json")]),
    /No \.txt transcripts/,
  );
  assert.throws(
    () => prepareSourceDirectory([file("Q&A/Test.txt")]),
    /directly contains the Garrett, Afyz, Erik, Splash and Zay folders/,
  );
});

test("unknown mentors and mixed roots remain rejected", () => {
  assert.throws(
    () => prepareSourceDirectory([file("GxT-Transcripts/Unknown/Test.txt")]),
    /unrecognized mentor folder: 'Unknown'/,
  );
  assert.throws(
    () => prepareSourceDirectory([
      file("GxT-Transcripts/Erik/Test.txt"),
      file("Other/Erik/Test.txt"),
    ]),
    /one transcript root/,
  );
});

test("review ordering keeps primary mentor labels human-readable", () => {
  const libraries = ["Zay", "Afyz", "Garrett", "Theo Notes"].map((name) => ({ display_name: `${name} — GxT` }));
  libraries.sort((left, right) => sourceLibraryOrder(left) - sourceLibraryOrder(right));
  assert.deepEqual(libraries.map((item) => item.display_name.split(" — ")[0]), ["Garrett", "Afyz", "Zay", "Theo Notes"]);
});
