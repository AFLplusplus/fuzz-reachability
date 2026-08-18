; Bitcode built with the optimizer on: the compile unit's debug info records
; isOptimized, and no definition carries noinline/optnone. That pair is the
; fingerprint the driver warns about, because an optimized module has already
; lost functions the source defines and no longer joins to llvm-cov by name.

define dso_local i32 @entry() !dbg !6 {
  %1 = call i32 @helper(), !dbg !9
  ret i32 %1, !dbg !9
}

define internal i32 @helper() !dbg !10 {
  ret i32 1, !dbg !11
}

!llvm.dbg.cu = !{!0}
!llvm.module.flags = !{!3, !4}

!0 = distinct !DICompileUnit(language: DW_LANG_C11, file: !1, producer: "clang", isOptimized: true, runtimeVersion: 0, emissionKind: FullDebug, splitDebugInlining: false, nameTableKind: None)
!1 = !DIFile(filename: "opt.c", directory: "/src")
!3 = !{i32 7, !"Dwarf Version", i32 5}
!4 = !{i32 2, !"Debug Info Version", i32 3}
!5 = !DISubroutineType(types: !2)
!2 = !{null}
!6 = distinct !DISubprogram(name: "entry", scope: !1, file: !1, line: 3, type: !5, scopeLine: 3, flags: DIFlagPrototyped, spFlags: DISPFlagDefinition, unit: !0)
!9 = !DILocation(line: 4, column: 3, scope: !6)
!10 = distinct !DISubprogram(name: "helper", scope: !1, file: !1, line: 8, type: !5, scopeLine: 8, spFlags: DISPFlagDefinition | DISPFlagLocalToUnit, unit: !0)
!11 = !DILocation(line: 9, column: 3, scope: !10)
