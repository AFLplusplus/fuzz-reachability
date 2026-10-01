%class.Parser = type { ptr, i64 }

define i32 @LLVMFuzzerTestOneInput(ptr %d, i64 %n) !dbg !10 {
  %p = alloca %class.Parser
  call void @_ZN6ParserC2EPKhm(ptr %p, ptr %d, i64 %n), !dbg !11
  call void @_ZN6Parser3runEv(ptr %p), !dbg !11
  %c = call i64 @_ZNK6Parser5countEv(ptr %p), !dbg !11
  call void @_ZN7Counter4bumpEv(ptr %p), !dbg !11
  call void @_ZN2ns4peekEPKh(ptr %d), !dbg !11
  call void @_ZN6Parser4skipEv(ptr %p), !dbg !11
  call void @_ZN12_GLOBAL__N_15Local4feedEPKhm(ptr %d, i64 %n), !dbg !11
  ret i32 0, !dbg !11
}

define void @_ZN6ParserC2EPKhm(ptr %this, ptr %d, i64 %n) !dbg !20 {
  ret void, !dbg !21
}

define void @_ZN6Parser3runEv(ptr %this) !dbg !30 {
  call void @_ZN6Parser4stepEv(ptr %this), !dbg !31
  ret void, !dbg !31
}

define void @_ZN6Parser4stepEv(ptr %this) !dbg !50 {
  call void @_ZN6Parser6headerEPKh(ptr %this, ptr null), !dbg !51
  ret void, !dbg !51
}

define void @_ZN6Parser4skipEv(ptr %this) !dbg !60 {
  ret void, !dbg !61
}

define internal void @_ZN12_GLOBAL__N_15Local4feedEPKhm(ptr %d, i64 %n) !dbg !70 {
  ret void, !dbg !71
}

define void @_ZN6Parser6headerEPKh(ptr %this, ptr %buf) !dbg !40 {
  ret void, !dbg !41
}

define i64 @_ZNK6Parser5countEv(ptr %this) {
  ret i64 0
}

define void @_ZN7Counter4bumpEv(ptr %this) {
  ret void
}

define void @_ZN2ns4peekEPKh(ptr %d) {
  ret void
}

!llvm.dbg.cu = !{!1}
!llvm.module.flags = !{!0}

!0 = !{i32 2, !"Debug Info Version", i32 3}
!1 = distinct !DICompileUnit(language: DW_LANG_C_plus_plus_14, file: !2, producer: "test", isOptimized: false, runtimeVersion: 0, emissionKind: FullDebug)
!2 = !DIFile(filename: "parser.cpp", directory: "/src")
!3 = !DIBasicType(name: "int", size: 32, encoding: DW_ATE_signed)
!4 = !DIBasicType(name: "unsigned char", size: 8, encoding: DW_ATE_unsigned_char)
!5 = !DIDerivedType(tag: DW_TAG_pointer_type, baseType: !4, size: 64)
!6 = !DIBasicType(name: "unsigned long", size: 64, encoding: DW_ATE_unsigned)
!7 = !DICompositeType(tag: DW_TAG_class_type, name: "Parser", file: !2, line: 1, size: 128, flags: DIFlagTypePassByValue, elements: !{}, identifier: "_ZTS6Parser")
!8 = !DIDerivedType(tag: DW_TAG_pointer_type, baseType: !7, size: 64, flags: DIFlagArtificial | DIFlagObjectPointer)
!10 = distinct !DISubprogram(name: "LLVMFuzzerTestOneInput", scope: !2, file: !2, line: 10, type: !12, spFlags: DISPFlagDefinition, unit: !1)
!11 = !DILocation(line: 11, scope: !10)
!12 = !DISubroutineType(types: !{!3, !5, !6})
!20 = distinct !DISubprogram(name: "Parser", linkageName: "_ZN6ParserC2EPKhm", scope: !7, file: !2, line: 2, type: !22, spFlags: DISPFlagDefinition, unit: !1)
!21 = !DILocation(line: 2, scope: !20)
!22 = !DISubroutineType(types: !{null, !8, !5, !6})
!30 = distinct !DISubprogram(name: "run", linkageName: "_ZN6Parser3runEv", scope: !7, file: !2, line: 3, type: !32, spFlags: DISPFlagDefinition, unit: !1)
!31 = !DILocation(line: 3, scope: !30)
!32 = !DISubroutineType(types: !{null, !8})
!40 = distinct !DISubprogram(name: "header", linkageName: "_ZN6Parser6headerEPKh", scope: !7, file: !2, line: 4, type: !42, spFlags: DISPFlagDefinition, unit: !1)
!41 = !DILocation(line: 4, scope: !40)
!42 = !DISubroutineType(types: !{null, !8, !5})
!50 = distinct !DISubprogram(name: "step", linkageName: "_ZN6Parser4stepEv", scope: !7, file: !2, line: 5, type: !32, spFlags: DISPFlagDefinition, unit: !1)
!51 = !DILocation(line: 5, scope: !50)
!60 = distinct !DISubprogram(name: "skip", linkageName: "_ZN6Parser4skipEv", scope: !7, file: !2, line: 6, type: !62, spFlags: DISPFlagDefinition, unit: !1)
!61 = !DILocation(line: 6, scope: !60)
!62 = !DISubroutineType(types: !{})
!70 = distinct !DISubprogram(name: "feed", linkageName: "_ZN12_GLOBAL__N_15Local4feedEPKhm", scope: !2, file: !2, line: 7, type: !22, spFlags: DISPFlagDefinition | DISPFlagLocalToUnit, unit: !1)
!71 = !DILocation(line: 7, scope: !70)
