class Transcodex < Formula
  desc "TUI to convert DVD/Blu-ray ISOs and video files to Apple TV compatible .m4v"
  homepage "https://github.com/mgoreiro/transcodex"
  url "https://github.com/mgoreiro/transcodex/archive/refs/tags/v1.3.0.tar.gz"
  sha256 "90113ae9437bcf23b8d9345b0ad31569e5c5d599ed36b8bcc8d2478956176912"
  license "GPL-3.0-or-later"

  depends_on "handbrake"
  depends_on "python@3.13"

  def install
    inreplace "transcodex.py", "#!/usr/bin/env python3", "#!#{Formula["python@3.13"].opt_bin}/python3.13"
    bin.install "transcodex.py" => "transcodex"
  end

  test do
    assert_match "transcodex #{version}", shell_output("#{bin}/transcodex --version")
  end
end
