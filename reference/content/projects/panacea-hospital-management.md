---
title: "Panacea Hospital Management System"
description: "A full-stack hospital operations platform (React + Express + MongoDB) for patient registry, appointments, medical records, and ward occupancy. Reference implementation for sensitive-data systems: role-based access control, audit logging, input validation, rate limiting, and soft-delete patterns built in from the start."
date: "2025-12-15"
author: "HephtronCode"
tags: ["healthcare", "React", "Express", "MongoDB", "RBAC", "audit logging", "production"]
difficulty: "intermediate"
---

**Panacea** is a modern, full-stack hospital management platform for streamlined patient care, appointment scheduling, and clinical operations.

**Why it's useful:** It doubles as a **reference implementation** for building **sensitive-data systems**. Production-grade structure is built in from the start:

- **Role-based access control (RBAC)**: Admin, Doctor, Nurse, Receptionist, Patient roles with strict endpoint protection
- **Audit logging**: Major actions logged for compliance (AuditLog collection)
- **Input validation**: Mongoose schemas plus centralized validators
- **Rate limiting**: Brute-force and DoS protection
- **Soft deletes**: Critical entities (e.g. Patient) use `isDeleted` with transparent filtering so data is never hard-deleted
- **Security**: Helmet, CORS, HPP, consistent API response shapes

**Core features:** Landing page, authentication, patient registry with medical history, appointments (schedule and status), medical records, ward management (bed occupancy), analytics dashboard.

**Tech stack:** React 19 + Vite, TanStack Query, React Router, Tailwind + shadcn/ui, Recharts; Node.js + Express 5, MongoDB + Mongoose, JWT, Winston + Morgan. Docker Compose for production-like runs.

[View on GitHub](https://github.com/HephtronCode/panacea-emr-project) · [Live demo (Vercel)](https://panacea-emr-project.vercel.app/)
