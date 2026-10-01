define internal i32 @_ZL5parsePKhm(ptr %p, i64 %n) {
  ret i32 0
}

define internal i32 @"_ZZ4mainENK3$_0clEPKhm"(ptr %self, ptr %d, i64 %n) {
  %r = call i32 @_ZL5parsePKhm(ptr %d, i64 %n)
  ret i32 %r
}

define internal i32 @"_ZZ4mainEN3$_08__invokeEPKhm"(ptr %d, i64 %n) {
  %r = call i32 @"_ZZ4mainENK3$_0clEPKhm"(ptr null, ptr %d, i64 %n)
  ret i32 %r
}

define internal i32 @_ZL5drivePFiPKhmE(ptr %cb) {
  %buf = alloca [4 x i8]
  %r = call i32 %cb(ptr %buf, i64 4)
  ret i32 %r
}

define i32 @main() {
  %r = call i32 @_ZL5drivePFiPKhmE(ptr @"_ZZ4mainEN3$_08__invokeEPKhm")
  ret i32 %r
}
