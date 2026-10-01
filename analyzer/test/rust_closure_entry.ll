define internal void @_ZN6zshape9fuzz_loop17h0123456789abcdefE() {
  %buf = alloca [16 x i8]
  call void @"_ZN6zshape4main28_$u7b$$u7b$closure$u7d$$u7d$17h1111111111111111E"(ptr null, ptr %buf, i64 16)
  ret void
}

define internal void @"_ZN6zshape4main28_$u7b$$u7b$closure$u7d$$u7d$17h1111111111111111E"(ptr %self, ptr %data, i64 %len) {
  %r = call i64 @_ZN6zshape5parse17h2222222222222222E(ptr %data, i64 %len)
  ret void
}

define internal i64 @_ZN6zshape5parse17h2222222222222222E(ptr %data, i64 %len) {
  ret i64 %len
}

define internal void @_ZN6zshape4main17h3333333333333333E() {
  call void @_ZN6zshape9fuzz_loop17h0123456789abcdefE()
  ret void
}
