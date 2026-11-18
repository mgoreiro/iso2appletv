class Transcodex < Formula
  desc "TUI to convert DVD/Blu-ray ISOs and video files to Apple TV compatible .m4v"
  homepage "https://github.com/mgoreiro/transcodex"
  url "https://github.com/mgoreiro/transcodex/archive/refs/tags/v1.3.1.tar.gz"
  sha256 "61c2f6e9181be5604a4692c3a65048b8cd575fa4bf9e74a942726cc0d5c187f2"
  license "GPL-3.0-or-later"

  depends_on "handbrake"
  depends_on "python@3.13"

  def install
    inreplace "transcodex.py", "#!/usr/bin/env python3", "#!#{formula_opt_bin("python@3.13")}/python3.13"
    bin.install "transcodex.py" => "transcodex"
  end

  test do
    assert_match "transcodex #{version}", shell_output("#{bin}/transcodex --version")
  end
end
