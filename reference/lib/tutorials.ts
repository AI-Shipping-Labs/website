import fs from "fs"
import path from "path"
import matter from "gray-matter"
import { remark } from "remark"
import html from "remark-html"

const tutorialsDirectory = path.join(process.cwd(), "content/tutorials")

export interface Tutorial {
  slug: string
  title: string
  description: string
  date: string
  tags?: string[]
  readingTime?: string
  contentHtml: string
}

export interface TutorialMeta {
  slug: string
  title: string
  description: string
  date: string
  tags?: string[]
  readingTime?: string
}

function calculateReadingTime(content: string): string {
  const wordsPerMinute = 200
  const words = content.split(/\s+/).length
  const minutes = Math.ceil(words / wordsPerMinute)
  return `${minutes} min read`
}

export async function getAllTutorials(): Promise<TutorialMeta[]> {
  if (!fs.existsSync(tutorialsDirectory)) {
    return []
  }

  const fileNames = fs.readdirSync(tutorialsDirectory)
  const allTutorialsData = fileNames
    .filter((fileName) => fileName.endsWith(".md"))
    .map((fileName) => {
      const slug = fileName.replace(/\.md$/, "")
      const fullPath = path.join(tutorialsDirectory, fileName)
      const fileContents = fs.readFileSync(fullPath, "utf8")
      const { data, content } = matter(fileContents)

      return {
        slug,
        title: data.title || slug,
        description: data.description || "",
        date: data.date || new Date().toISOString(),
        tags: data.tags || [],
        readingTime: calculateReadingTime(content),
      }
    })

  return allTutorialsData.sort((a, b) => (a.date < b.date ? 1 : -1))
}

export async function getTutorialBySlug(slug: string): Promise<Tutorial | null> {
  if (!fs.existsSync(tutorialsDirectory)) {
    return null
  }

  const fullPath = path.join(tutorialsDirectory, `${slug}.md`)
  
  if (!fs.existsSync(fullPath)) {
    return null
  }

  const fileContents = fs.readFileSync(fullPath, "utf8")
  const { data, content } = matter(fileContents)

  const processedContent = await remark().use(html).process(content)
  const contentHtml = processedContent.toString()

  return {
    slug,
    title: data.title || slug,
    description: data.description || "",
    date: data.date || new Date().toISOString(),
    tags: data.tags || [],
    readingTime: calculateReadingTime(content),
    contentHtml,
  }
}
