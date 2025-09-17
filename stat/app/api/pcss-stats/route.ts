import { NextResponse } from "next/server"
import { MongoClient } from "mongodb"

// Environment variables
const MONGODB_URI = process.env.MONGODB_URI || "mongodb://localhost:27017"
const DB_NAME = process.env.DB_NAME || "pcss"
const LOG_COLLECTION = process.env.LOG_COLLECTION || "logs"

// IP normalization function (same as Python version)
function normIp(ip: string): string {
  if (ip && ip.startsWith("::ffff:")) {
    return ip.split("::ffff:")[1]
  }
  return ip || "-"
}

// Safe object access function
function safeGet(obj: any, ...keys: string[]): any {
  let current = obj
  for (const key of keys) {
    if (!current || typeof current !== "object" || !(key in current)) {
      return null
    }
    current = current[key]
  }
  return current
}

export async function GET() {
  let client: MongoClient | null = null

  try {
    // Connect to MongoDB
    client = new MongoClient(MONGODB_URI)
    await client.connect()

    const db = client.db(DB_NAME)
    const collection = db.collection(LOG_COLLECTION)

    // Fetch all documents
    const docs = await collection
      .find(
        {},
        {
          projection: { _id: 0, date: 1, logs: 1 },
        },
      )
      .toArray()

    if (docs.length === 0) {
      return NextResponse.json({ error: "No data found" }, { status: 404 })
    }

    // Initialize counters
    let totalRequests = 0
    const byDate: { [key: string]: number } = {}
    const byIp: { [key: string]: number } = {}
    const byOption: { [key: string]: number } = {}
    const byCountOption: { [key: string]: number } = {}
    const byUncertainty: { [key: string]: number } = {}
    const byConference: { [key: string]: number } = {}
    const byHour: { [key: string]: number } = {}

    let firstTs: Date | null = null
    let lastTs: Date | null = null

    // Process each document
    for (const doc of docs) {
      const docDate = doc.date || "-"
      const logs = doc.logs || []

      const filteredLogs = logs.filter((item: any) => {
        const ip = normIp(item.ip)
        return ip !== "172.17.113.140"
      })

      byDate[docDate] = (byDate[docDate] || 0) + filteredLogs.length
      totalRequests += filteredLogs.length

      // Process each log entry (using filtered logs)
      for (const item of filteredLogs) {
        const ip = normIp(item.ip)
        const data = item.data || {}

        // Parse timestamp and convert to Korean time
        const timeStr = item.time
        let ts: Date | null = null
        try {
          if (timeStr) {
            ts = new Date(timeStr.replace("Z", "+00:00"))

            const koreanTs = new Date(ts.getTime() + 9 * 60 * 60 * 1000)

            if (!firstTs || koreanTs < firstTs) firstTs = koreanTs
            if (!lastTs || koreanTs > lastTs) lastTs = koreanTs

            const hourKey = koreanTs.toISOString().substring(0, 13) + ":00"
            byHour[hourKey] = (byHour[hourKey] || 0) + 1
          }
        } catch (e) {
          // Invalid timestamp, skip
        }

        // Count by various dimensions
        byIp[ip] = (byIp[ip] || 0) + 1

        const option = String(safeGet(data, "option") || "-")
        byOption[option] = (byOption[option] || 0) + 1

        const countOption = String(safeGet(data, "CountOption") || "-").toLowerCase()
        byCountOption[countOption] = (byCountOption[countOption] || 0) + 1

        const uncertainty = String(safeGet(data, "uncertainty") || "-")
        byUncertainty[uncertainty] = (byUncertainty[uncertainty] || 0) + 1

        const conferences = safeGet(data, "selectedConferences")
        if (Array.isArray(conferences)) {
          for (const conf of conferences) {
            const confStr = String(conf)
            byConference[confStr] = (byConference[confStr] || 0) + 1
          }
        }
      }
    }

    // Convert counters to sorted arrays
    const sortByCount = (obj: { [key: string]: number }) =>
      Object.entries(obj)
        .map(([key, count]) => ({ key, count }))
        .sort((a, b) => b.count - a.count)

    const result = {
      totalRequests,
      totalDays: docs.length,
      dateRange: {
        start: firstTs ? firstTs.toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "-",
        end: lastTs ? lastTs.toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "-",
      },
      byDate: sortByCount(byDate),
      byIp: sortByCount(byIp),
      byOption: sortByCount(byOption),
      byCountOption: sortByCount(byCountOption),
      byUncertainty: sortByCount(byUncertainty),
      byConference: sortByCount(byConference),
      byHour: Object.entries(byHour)
        .map(([key, count]) => ({ key, count }))
        .sort((a, b) => a.key.localeCompare(b.key)),
    }

    return NextResponse.json(result)
  } catch (error) {
    console.error("MongoDB connection error:", error)
    return NextResponse.json(
      { error: "Database connection failed", details: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 },
    )
  } finally {
    if (client) {
      await client.close()
    }
  }
}
