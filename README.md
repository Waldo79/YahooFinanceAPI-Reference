# YahooFinanceAPI-Reference

A developer-oriented reference for the unofficial Yahoo Finance JSON endpoints, with a focus on endpoint documentation, JSON object and field dictionaries, security-type differences, `marketState` behavior, JSON-to-CSV mapping, validation rules, sample captures, and test cases.

Yahoo Finance does not publish these endpoints as a formal public API. This project documents observed behavior from captures and community-tested usage. Fields can appear, disappear, reorder, or vary by security type and market state.

## Current Status

Initial repository foundation: `v0.1.0`.

## Core Endpoints

- Quote
- QuoteSummary
- Chart
- Options
- Search
- Screener
- Spark

## Design Principle

Yahoo Finance behaves like a dynamic-schema API. Parsers should read fields by key name, treat most fields as conditional, log unknown fields, preserve raw captures, and validate derived values where possible.
