export const GXT_MENTORS = ["Garrett", "Afyz", "Erik", "Splash", "Zay", "Theo Notes"];

export function prepareSourceDirectory(selectedFiles) {
  const files = [...selectedFiles].filter((file) => file.name.toLowerCase().endsWith(".txt"));
  const ignoredCount = selectedFiles.length - files.length;
  if (!files.length) throw new Error("No .txt transcripts were found in this folder.");

  const paths = files.map((file) => file.webkitRelativePath.replaceAll("\\", "/").split("/").filter(Boolean));
  if (paths.some((parts) => parts.length < 3 || parts.includes(".."))) {
    throw new Error("This doesn't look like your GxT transcript root. Select the folder that directly contains the Garrett, Afyz, Erik, Splash and Zay folders.");
  }
  if (new Set(paths.map((parts) => parts[0].toLowerCase())).size !== 1) {
    throw new Error("Choose one transcript root at a time.");
  }
  const unknown = paths.find((parts) => !GXT_MENTORS.includes(parts[1]));
  if (unknown) {
    throw new Error(`This folder contains transcript files under an unrecognized mentor folder: '${unknown[1]}'. Expected Garrett, Afyz, Erik, Splash, Zay, or Theo Notes.`);
  }
  return { files, ignoredCount };
}

export function sourceLibraryOrder(library) {
  const mentor = library.display_name.split(" — ")[0];
  const index = GXT_MENTORS.indexOf(mentor);
  return index < 0 ? GXT_MENTORS.length : index;
}
