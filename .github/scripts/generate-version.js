if (process.argv.length < 4) {
    console.error("Usage: node generate-version [version] [patch|minor|major]");
    process.exit(1);
}

const version = process.argv[2];
const incrementType = process.argv[3];

const versionSplit = version.split(".");
if (versionSplit.length !== 3) {
    console.error("Invalid semantic version");
    process.exit(1);
}

let majorVersion = parseInt(versionSplit[0]);
let minorVersion = parseInt(versionSplit[1]);
let patchVersion = parseInt(versionSplit[2]);

if (isNaN(majorVersion) || isNaN(minorVersion) || isNaN(patchVersion)) {
    console.error("Invalid semantic version");
    process.exit(1);
}

if (majorVersion < 0 || minorVersion < 0 || patchVersion < 0) {
    console.error("Invalid semantic version");
    process.exit(1);
}

if (incrementType === "major") {
    majorVersion += 1;
    minorVersion = 0;
    patchVersion = 0;
    console.log(majorVersion + "." + minorVersion + "." + patchVersion);
    process.exit(0);
} else if (incrementType === "minor") {
    minorVersion += 1;
    patchVersion = 0;
    console.log(majorVersion + "." + minorVersion + "." + patchVersion);
    process.exit(0);
} else if (incrementType === "patch") {
    patchVersion += 1;
    console.log(majorVersion + "." + minorVersion + "." + patchVersion);
    process.exit(0);
} else {
    console.error("Invalid increment type: " + incrementType + ", allowed values are patch, minor, major");
    process.exit(1);
}
