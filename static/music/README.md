# Music Folder

Drop real album folders here. Each album folder should contain:
  - cover.jpg (album art — shown instead of generated canvas art)
  - 01 - Track Name.mp3 (numbered mp3 files)

Singles go at root level with a matching jpg:
  - Wanderer.mp3
  - Wanderer.jpg

See mock_data/music_library.json for the data schema.
The music.js module will try to load cover images from artUrl; if absent it
generates procedural canvas art from the artColor palette defined in the data.
