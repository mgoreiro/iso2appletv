# Genera docs/demo.gif (necesita vhs, ffmpeg y HandBrakeCLI):
#   docs/demo-setup.sh && PATH="$PWD/bin-demo:$PATH" vhs docs/demo.tape
# donde bin-demo/transcodex es un enlace a transcodex.py (o transcodex ya instalado).
Output docs/demo.gif
Set Shell "bash"
Set FontSize 16
Set Width 1200
Set Height 700
Set Padding 12
Set TypingSpeed 60ms

Hide
Type "cd /tmp/transcodex-demo && PS1='$ ' && clear" Enter
Sleep 1s
Show

Type "transcodex -o out in/ -y" Sleep 500ms Enter
Sleep 12s
Type "l" Sleep 3s
Type "l" Sleep 2s
Type "?" Sleep 3s
Type "?" Sleep 1s
Type "q" Sleep 1s
