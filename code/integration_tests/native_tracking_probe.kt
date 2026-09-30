package com.fypproject.tracking

import org.json.JSONArray
import org.json.JSONObject

fun main() {
    val arguments = JSONArray(generateSequence(::readLine).joinToString("\n"))
    val results = JSONArray()
    fun integer(value: Any?): Long {
        require(value is Long || value is Int) { "A native integer timestamp is required" }
        return (value as Number).toLong()
    }
    for (index in 0 until arguments.length()) {
        val result = JSONObject()
        try {
            val row = arguments.getJSONArray(index)
            val point = row.getJSONObject(0)
            val captured = integer(point.opt("timestamp"))
            val started = integer(row.get(1))
            val now = integer(row.get(2))
            val previous = if (row.isNull(3)) Long.MIN_VALUE else integer(row.get(3))
            val latitude = point.optDouble("latitude", 22.3)
            val longitude = point.optDouble("longitude", 114.2)
            require(TrackingCore.validPoint(latitude, longitude, captured, previous, started, now)) {
                "Native TrackingCore rejected the fix"
            }
            result.put("accepted", true)
            result.put("value", JSONObject().put("milliseconds", captured))
        } catch (error: Exception) {
            result.put("accepted", false)
            result.put("error", error.message ?: "Invalid probe input")
        }
        results.put(result)
    }
    println(results.toString())
}
