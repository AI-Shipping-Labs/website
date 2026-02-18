"use client"

import { useState } from "react"
import { VideoPlayer } from "./video-player"
import { TimestampLink } from "./timestamp-link"

interface ResourceVideoSectionProps {
  googleEmbedUrl?: string
  youtubeUrl?: string
  title: string
  videoId: string
  timestamps?: Array<{
    time: string
    title: string
    description?: string
  }>
}

export function ResourceVideoSection({ 
  googleEmbedUrl, 
  youtubeUrl, 
  title, 
  videoId,
  timestamps 
}: ResourceVideoSectionProps) {
  const [currentTime, setCurrentTime] = useState<number | undefined>(undefined)
  const isYouTube = !!youtubeUrl

  const handleSeek = (seconds: number) => {
    if (isYouTube) {
      setCurrentTime(seconds)
    }
  }

  return (
    <>
      {(googleEmbedUrl || youtubeUrl) && (
        <div className="mb-12 rounded-lg border border-border bg-card p-4">
          <VideoPlayer
            googleEmbedUrl={googleEmbedUrl}
            youtubeUrl={youtubeUrl}
            title={title}
            videoId={videoId}
            currentTime={currentTime}
          />
        </div>
      )}

      {timestamps && timestamps.length > 0 && (
        <div className="mb-12">
          <h2 className="mb-4 text-2xl font-semibold tracking-tight">Timestamps</h2>
          {isYouTube && (
            <p className="mb-4 text-sm text-muted-foreground">
              Click any timestamp to jump to that moment in the video
            </p>
          )}
          <div className="space-y-3">
            {timestamps.map((timestamp, index) => (
              <TimestampLink
                key={index}
                time={timestamp.time}
                title={timestamp.title}
                description={timestamp.description}
                videoId={videoId}
                isYouTube={isYouTube}
                onSeek={handleSeek}
              />
            ))}
          </div>
        </div>
      )}
    </>
  )
}
