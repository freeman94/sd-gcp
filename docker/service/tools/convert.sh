#!/bin/bash
$(ffmpeg -i $1 -vcodec copy -acodec copy $2.avi -y)
