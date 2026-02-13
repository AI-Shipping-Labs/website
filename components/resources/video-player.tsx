"use client"

import { useState, useEffect } from "react"

interface VideoPlayerProps {
  googleEmbedUrl?: string
  youtubeUrl?: string
  title: string
  videoId: string
  currentTime?: number
}

export function VideoPlayer({ googleEmbedUrl, youtubeUrl, title, videoId, currentTime }: VideoPlayerProps) {
  const [embedUrl, setEmbedUrl] = useState<string>("")

  useEffect(() => {
    const getEmbedUrl = (timeInSeconds?: number) => {
      if (googleEmbedUrl) {
        // For Google Drive embeds, we can't easily control playback time
        return googleEmbedUrl
      }
      
      if (youtubeUrl) {
        let url = youtubeUrl
          .replace("watch?v=", "embed/")
          .replace("youtu.be/", "youtube.com/embed/")
        
        // Remove existing time parameters
        url = url.split("&t=")[0].split("?t=")[0].split("&start=")[0]
        
        // Add time parameter if provided
        if (timeInSeconds !== undefined && timeInSeconds > 0) {
          const separator = url.includes("?") ? "&" : "?"
          url = `${url}${separator}start=${timeInSeconds}`
        }
        
        return url
      }
      
      return ""
    }

    setEmbedUrl(getEmbedUrl(currentTime))
  }, [googleEmbedUrl, youtubeUrl, currentTime])

  if (!googleEmbedUrl && !youtubeUrl) {
    return null
  }

  if (!embedUrl) {
    return null
  }

  return (
    <div className="aspect-video w-full overflow-hidden rounded-lg">
      <iframe
        key={embedUrl} // Force re-render when URL changes
        src={embedUrl}
        className="h-full w-full"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        allowFullScreen
        title={title}
      />
    </div>
  )
}
