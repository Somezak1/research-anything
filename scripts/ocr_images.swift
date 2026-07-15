#!/usr/bin/env swift
import AppKit
import Foundation
import Vision

struct OCRResult: Codable {
    let path: String
    let status: String
    let text: String
    let error: String?
}

func recognize(_ path: String) -> OCRResult {
    guard let image = NSImage(contentsOfFile: path) else {
        return OCRResult(path: path, status: "failed", text: "", error: "无法读取图片")
    }
    var rect = NSRect(origin: .zero, size: image.size)
    guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
        return OCRResult(path: path, status: "failed", text: "", error: "无法解码图片")
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
    do {
        try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
        let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
        return OCRResult(path: path, status: "ocr", text: lines.joined(separator: "\n"), error: nil)
    } catch {
        return OCRResult(path: path, status: "failed", text: "", error: error.localizedDescription)
    }
}

let paths = Array(CommandLine.arguments.dropFirst())
guard !paths.isEmpty else {
    fputs("用法: ocr_images.swift <图片路径...>\n", stderr)
    exit(2)
}

let results = paths.map(recognize)
let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
do {
    let data = try encoder.encode(results)
    print(String(decoding: data, as: UTF8.self))
    if results.contains(where: { $0.status == "failed" }) {
        exit(2)
    }
} catch {
    fputs("JSON 编码失败: \(error)\n", stderr)
    exit(2)
}
