"use client"

import { useState, useEffect } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  LineChart,
  Line,
  PieChart,
  Pie,
} from "recharts"
import { RefreshCw, Database, Calendar, Users, Clock, TrendingUp } from "lucide-react"

interface LogStats {
  totalRequests: number
  totalDays: number
  dateRange: {
    start: string
    end: string
  }
  byDate: Array<{ key: string; count: number }>
  byIp: Array<{ key: string; count: number }>
  byOption: Array<{ key: string; count: number }>
  byCountOption: Array<{ key: string; count: number }>
  byUncertainty: Array<{ key: string; count: number }>
  byConference: Array<{ key: string; count: number }>
  byHour: Array<{ key: string; count: number }>
}

const COLORS = ["#0088FE", "#00C49F", "#FFBB28", "#FF8042", "#8884D8", "#82CA9D", "#FFC658", "#FF7C7C"]

export default function PCSSStatsPage() {
  const [stats, setStats] = useState<LogStats | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)

  const fetchStats = async () => {
    setLoading(true)
    setError(null)

    try {
      const response = await fetch("/api/pcss-stats")
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }

      const data = await response.json()
      setStats(data)
      setLastUpdated(new Date().toLocaleString("ko-KR"))
    } catch (err) {
      setError(err instanceof Error ? err.message : "데이터를 불러오는데 실패했습니다.")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchStats()
  }, [])

  const renderTopTable = (title: string, data: Array<{ key: string; count: number }>, limit = 10) => {
    if (!data || data.length === 0) {
      return (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">{title}</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-center text-muted-foreground py-8">데이터가 없습니다</div>
          </CardContent>
        </Card>
      )
    }

    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">{title}</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-12">#</TableHead>
                <TableHead>Key</TableHead>
                <TableHead className="text-right">Count</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.slice(0, limit).map((item, index) => (
                <TableRow key={item.key}>
                  <TableCell className="font-medium">{index + 1}</TableCell>
                  <TableCell className="font-mono text-sm">{item.key}</TableCell>
                  <TableCell className="text-right font-semibold">{item.count.toLocaleString()}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    )
  }

  const renderChart = (
    title: string,
    data: Array<{ key: string; count: number }>,
    type: "bar" | "line" | "pie" = "bar",
    limit = 10,
  ) => {
    if (!data || data.length === 0) {
      return (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">{title}</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="h-80 flex items-center justify-center text-muted-foreground">데이터가 없습니다</div>
          </CardContent>
        </Card>
      )
    }

    const chartData = data.slice(0, limit).map((item) => ({
      name: item.key.length > 20 ? item.key.substring(0, 20) + "..." : item.key,
      value: item.count,
      fullName: item.key,
    }))

    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">{title}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              {type === "bar" ? (
                <BarChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="name" angle={-45} textAnchor="end" height={80} fontSize={12} />
                  <YAxis />
                  <Tooltip
                    formatter={(value: any, _name: any, props: any) => [
                      (Number(value) ?? 0).toLocaleString(),
                      props?.payload?.fullName ?? "",
                    ]}
                  />
                  <Bar dataKey="value" />
                </BarChart>
              ) : type === "line" ? (
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="name" angle={-45} textAnchor="end" height={80} fontSize={12} />
                  <YAxis />
                  <Tooltip
                    formatter={(value: any, _name: any, props: any) => [
                      (Number(value) ?? 0).toLocaleString(),
                      props?.payload?.fullName ?? "",
                    ]}
                  />
                  <Line type="monotone" dataKey="value" strokeWidth={2} />
                </LineChart>
              ) : (
                <PieChart>
                  <Pie
                    data={chartData}
                    cx="50%"
                    cy="50%"
                    labelLine={false}
                    label={({ name, percent }) => `${name} ${(percent * 100).toFixed(1)}%`}
                    outerRadius={80}
                    dataKey="value"
                  />
                  <Tooltip formatter={(value: any) => [(Number(value) ?? 0).toLocaleString(), "Requests"]} />
                </PieChart>
              )}
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>
    )
  }

  if (error) {
    return (
      <div className="container mx-auto p-6">
        <Card className="border-destructive">
          <CardHeader>
            <CardTitle className="text-destructive flex items-center gap-2">
              <Database className="h-5 w-5" />
              연결 오류
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground mb-4">{error}</p>
            <Button onClick={fetchStats} disabled={loading}>
              <RefreshCw className={`h-4 w-4 mr-2 ${loading ? "animate-spin" : ""}`} />
              다시 시도
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="container mx-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-balance">PCSS 로그 분석 대시보드</h1>
        </div>
        <div className="flex items-center gap-4">
          {lastUpdated && (
            <Badge variant="outline" className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {lastUpdated}
            </Badge>
          )}
          <Button onClick={fetchStats} disabled={loading} size="sm">
            <RefreshCw className={`h-4 w-4 mr-2 ${loading ? "animate-spin" : ""}`} />
            새로고침
          </Button>
        </div>
      </div>

      {/* Summary Cards */}
      {stats && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">총 요청 수</CardTitle>
                <TrendingUp className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{stats.totalRequests.toLocaleString()}</div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">분석 기간 (일)</CardTitle>
                <Calendar className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{stats.totalDays.toLocaleString()}</div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">고유 IP 수</CardTitle>
                <Users className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{(stats.byIp?.length || 0).toLocaleString()}</div>
              </CardContent>
            </Card>
          </div>

          {/* Date Range */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Calendar className="h-5 w-5" />
                분석 기간
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                <span className="font-mono">{stats.dateRange.start}</span> ~{" "}
                <span className="font-mono">{stats.dateRange.end}</span>
              </p>
            </CardContent>
          </Card>

          <Tabs defaultValue="overview" className="space-y-4">
            <TabsList className="grid w-full grid-cols-3">
              <TabsTrigger value="overview">개요</TabsTrigger>
              <TabsTrigger value="options">옵션 분석</TabsTrigger>
              <TabsTrigger value="conferences">컨퍼런스</TabsTrigger>
            </TabsList>

            <TabsContent value="overview" className="space-y-6">
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {renderChart("일별 요청 수", stats.byDate || [], "line", 30)}
                {renderChart("시간대별 요청 수 (UTC)", stats.byHour || [], "bar", 24)}
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {renderTopTable("요청 수 (IP별)", stats.byIp || [], 15)}
                {renderChart("상위 IP", stats.byIp || [], "pie", 8)}
              </div>
            </TabsContent>

            <TabsContent value="options" className="space-y-6">
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {renderTopTable("옵션(option) 분포", stats.byOption || [])}
                {renderTopTable("CountOption 분포", stats.byCountOption || [])}
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {renderTopTable("Uncertainty 분포", stats.byUncertainty || [])}
                {renderChart("옵션 분포", stats.byOption || [], "pie")}
              </div>
            </TabsContent>

            <TabsContent value="conferences" className="space-y-6">
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {renderTopTable("선택된 컨퍼런스", stats.byConference || [], 20)}
                {renderChart("상위 컨퍼런스", stats.byConference || [], "bar", 15)}
              </div>
            </TabsContent>
          </Tabs>
        </>
      )}

      {loading && !stats && (
        <div className="flex items-center justify-center py-12">
          <div className="flex items-center gap-2">
            <RefreshCw className="h-5 w-5 animate-spin" />
            <span>데이터를 불러오는 중...</span>
          </div>
        </div>
      )}
    </div>
  )
}
