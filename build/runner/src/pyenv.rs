// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

use std::process::Command;

use camino::Utf8Path;
use clap::Args;

use crate::run::run_silent;

#[derive(Args)]
pub struct PyenvArgs {
    python_bin: String,
    pyenv_folder: String,
    initial_reqs: String,
    reqs: Vec<String>,
}

/// Set up a venv if one doesn't already exist, and then sync packages with provided requirements file.
pub fn setup_pyenv(args: PyenvArgs) {
    let pyenv_folder = Utf8Path::new(&args.pyenv_folder);

    let pyenv_bin_folder = pyenv_folder.join(if cfg!(windows) { "scripts" } else { "bin" });
    let pip = pyenv_bin_folder.join("pip");
    let pip_sync = pyenv_bin_folder.join("pip-sync");

    if !pip.exists() {
        run_silent(Command::new(&args.python_bin).args(["-m", "venv", pyenv_folder.as_str()]));

        if cfg!(windows) {
            // the first install on Windows throws an error the first time pip is upgraded, so we install
            // it twice and swallow the first error
            let _output = Command::new(&pip)
                .args(["install", "-r", &args.initial_reqs])
                .output()
                .unwrap();
        }

        run_silent(Command::new(pip).args(["install", "-r", &args.initial_reqs]));
    }

    run_silent(Command::new(pip_sync).args(&args.reqs));
}
