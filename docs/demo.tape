# Genera docs/demo.gif:  vhs docs/demo.tape   (necesita vhs, ffmpeg y HandBrakeCLI)
Output docs/demo.gif
Set Shell "bash"
Set FontSize 16
Set Width 1200
Set Height 700
Set Padding 12

Hide
Type "export PS1='$ ' && mkdir -p /tmp/transcodex-demo/in /tmp/transcodex-demo/out && cd /tmp/transcodex-demo" Enter
Type "for n in pelicula serie-e01 serie-e02; do ffmpeg -loglevel error -y -f lavfi -i testsrc=duration=20:size=1280x720:rate=24 -f lavfi -i sine=duration=20 -c:v libx264 -c:a aac in/$n.mkv; done" Enter
Type "clear" Enter
Show

Type "transcodex -o out in/ -y" Sleep 500ms Enter
Sleep 12s
Type "l" Sleep 3s
Type "l" Sleep 2s
Type "?" Sleep 3s
Type "?" Sleep 1s
Type "q" Sleep 1s
