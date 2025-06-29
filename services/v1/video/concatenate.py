# Copyright (c) 2025 Stephen G. Pope
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.



import os
import ffmpeg
import requests
from services.file_management import download_file
from config import LOCAL_STORAGE_PATH

def process_video_concatenate(media_urls, job_id, webhook_url=None, transitions=None):
    """Combine multiple videos into one with optional transitions.
    
    Args:
        media_urls: List of media items with video_url
        job_id: Unique job identifier
        webhook_url: Optional webhook URL for notifications
        transitions: List of transition objects with type and duration
    """
    input_files = []
    output_filename = f"{job_id}.mp4"
    output_path = os.path.join(LOCAL_STORAGE_PATH, output_filename)

    try:
        # Download all media files
        for i, media_item in enumerate(media_urls):
            url = media_item['video_url']
            input_filename = download_file(url, os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_input_{i}"))
            input_files.append(input_filename)

        # Check if transitions are specified and handle accordingly
        if transitions and len(transitions) > 0:
            # Use filtergraph for transitions
            _concatenate_with_transitions(input_files, output_path, transitions)
        else:
            # Use simple concat demuxer for fast concatenation
            _concatenate_simple(input_files, output_path, job_id)

        # Clean up input files
        for f in input_files:
            os.remove(f)

        print(f"Video combination successful: {output_path}")

        # Check if the output file exists locally before upload
        if not os.path.exists(output_path):
            raise FileNotFoundError(f"Output file {output_path} does not exist after combination.")

        return output_path
    except Exception as e:
        print(f"Video combination failed: {str(e)}")
        raise

def _concatenate_simple(input_files, output_path, job_id):
    """Simple concatenation using concat demuxer."""
    concat_file_path = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_concat_list.txt")
    
    with open(concat_file_path, 'w') as concat_file:
        for input_file in input_files:
            # Write absolute paths to the concat list
            concat_file.write(f"file '{os.path.abspath(input_file)}'\n")
    
    # Use the concat demuxer to concatenate the videos
    (
        ffmpeg.input(concat_file_path, format='concat', safe=0).
            output(output_path, c='copy').
            run(overwrite_output=True)
    )
    
    # Clean up concat file
    os.remove(concat_file_path)

def _concatenate_with_transitions(input_files, output_path, transitions):
    """Concatenate videos with transitions using filtergraph."""
    if len(input_files) < 2:
        raise ValueError("At least 2 videos are required for transitions")
    
    # Load all input files
    inputs = [ffmpeg.input(file) for file in input_files]
    
    # Build filtergraph for transitions
    if len(input_files) == 2:
        # Single transition between two videos
        transition = transitions[0] if transitions else {'type': 'none', 'duration': 1.0}
        output_streams = _apply_transition(inputs[0], inputs[1], transition)
    else:
        # Multiple transitions - chain them
        result = inputs[0]
        
        for i in range(1, len(inputs)):
            transition_idx = min(i - 1, len(transitions) - 1) if transitions else 0
            transition = transitions[transition_idx] if transitions else {'type': 'none', 'duration': 1.0}
            
            result = _apply_transition(result, inputs[i], transition)
    
        output_streams = result
    
    # Handle different return types from transition functions
    if isinstance(output_streams, dict):
        # Dictionary with 'video' and 'audio' keys
        video_stream = output_streams['video']
        audio_stream = output_streams['audio']
        ffmpeg.output(video_stream, audio_stream, output_path).run(overwrite_output=True)
    else:
        # Single stream or tuple - use as is
        ffmpeg.output(output_streams, output_path).run(overwrite_output=True)

def _get_video_duration(input_stream):
    """Get duration of a video stream in seconds."""
    # For ffmpeg input streams, we need to get the filename
    if hasattr(input_stream, 'node') and hasattr(input_stream.node, 'kwargs'):
        filename = input_stream.node.kwargs.get('filename')
        if filename:
            probe = ffmpeg.probe(filename)
            return float(probe['format']['duration'])
    return 0.0

def _apply_transition(video1, video2, transition):
    """Apply a specific transition between two video streams."""
    transition_type = transition.get('type', 'none')
    duration = float(transition.get('duration', 1.0))
    
    if transition_type == 'crossfade':
        return _crossfade_transition(video1, video2, duration)
    elif transition_type == 'fade':
        return _fade_transition(video1, video2, duration)
    elif transition_type == 'wipe':
        return _wipe_transition(video1, video2, duration)
    elif transition_type == 'slide':
        return _slide_transition(video1, video2, duration)
    else:  # 'none' or any other type defaults to simple concatenation
        return ffmpeg.concat(video1, video2, v=1, a=1)

def _crossfade_transition(video1, video2, duration):
    """Apply crossfade transition between two videos."""
    v1_duration = _get_video_duration(video1)
    offset = v1_duration - duration
    
    # Trim and fade out first video
    v1_trimmed = video1.video.filter('trim', duration=v1_duration)
    v1_faded = v1_trimmed.filter('fade', type='out', start_time=offset, duration=duration)
    a1_trimmed = video1.audio.filter('atrim', duration=v1_duration)
    a1_faded = a1_trimmed.filter('afade', type='out', start_time=offset, duration=duration)
    
    # Trim and fade in second video (take only the transition duration)
    v2_trimmed = video2.video.filter('trim', start_time=0, duration=duration)
    v2_faded = v2_trimmed.filter('fade', type='in', start_time=0, duration=duration)
    a2_trimmed = video2.audio.filter('atrim', start_time=0, duration=duration)
    a2_faded = a2_trimmed.filter('afade', type='in', start_time=0, duration=duration)
    
    # Get remaining part of second video
    v2_remaining = video2.video.filter('trim', start_time=duration)
    a2_remaining = video2.audio.filter('atrim', start_time=duration)
    
    # Overlay the faded portions
    v_overlay = ffmpeg.filter([v1_faded, v2_faded], 'overlay')
    a_mix = ffmpeg.filter([a1_faded, a2_faded], 'amix', inputs=2, duration='shortest')
    
    # Concatenate with remaining second video
    v_final = ffmpeg.concat(v_overlay, v2_remaining, v=1, a=0)
    a_final = ffmpeg.concat(a_mix, a2_remaining, v=0, a=1)
    
    return {'video': v_final, 'audio': a_final}

def _fade_transition(video1, video2, duration):
    """Apply fade to black transition between two videos."""
    v1_duration = _get_video_duration(video1)
    fade_start = v1_duration - duration/2
    
    # Fade out first video to black
    v1_faded = video1.video.filter('fade', type='out', start_time=fade_start, duration=duration/2)
    a1_faded = video1.audio.filter('afade', type='out', start_time=fade_start, duration=duration/2)
    
    # Fade in second video from black
    v2_faded = video2.video.filter('fade', type='in', start_time=0, duration=duration/2)
    a2_faded = video2.audio.filter('afade', type='in', start_time=0, duration=duration/2)
    
    # Concatenate the faded videos
    v_out = ffmpeg.concat(v1_faded, v2_faded, v=1, a=0)
    a_out = ffmpeg.concat(a1_faded, a2_faded, v=0, a=1)
    
    return {'video': v_out, 'audio': a_out}

def _wipe_transition(video1, video2, duration):
    """Apply wipe transition between two videos."""
    # Simple wipe effect - for now just do a basic concat
    # TODO: Implement proper wipe with mask overlay
    v_out = ffmpeg.concat(video1, video2, v=1, a=0)
    a_out = ffmpeg.concat(video1, video2, v=0, a=1)
    
    return {'video': v_out, 'audio': a_out}

def _slide_transition(video1, video2, duration):
    """Apply slide transition between two videos."""
    # Simple slide effect - for now just do a basic concat
    # TODO: Implement proper slide with animated overlay
    v_out = ffmpeg.concat(video1, video2, v=1, a=0)
    a_out = ffmpeg.concat(video1, video2, v=0, a=1)
    
    return {'video': v_out, 'audio': a_out}
